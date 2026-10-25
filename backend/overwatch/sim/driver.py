"""Simulation driver: advances the synthetic world on a fixed step and emits sensor packets on each sensor's own
schedule (drone video 15 FPS, phone 10 Hz, telemetry 10 Hz), applying the noise bridge (drops, latency, blur,
low light, wobble, gimbal pan, covered markers). Used identically by the paced server and the headless runner."""

from __future__ import annotations

import copy
import logging

import math
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from ..config import Config
from ..faults import FaultInjector
from ..geometry.camera import Intrinsics, PinholeCamera
from ..sources.base import FramePacket, PhonePacket, TelemetryPacket
from .actors import Actor
from .bridge_sim import SimBridge
from .drone_dynamics import SimDrone
from .phone_sim import PhoneSim
from .render import RenderEffects, SceneRenderer
from .scenarios import Scenario, ScriptedAction, get_scenario
from .texture import GroundTexture, build_ground_texture, marker_world_corners
from .world import World, default_world


log = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _cached_texture(key: tuple) -> tuple[World, GroundTexture]:
    layout, size, geofence = key
    world = default_world({k: v for k, v in layout}, size, [tuple(p) for p in geofence])
    return world, build_ground_texture(world)


_WORLD_CACHE: dict[Any, tuple[World, GroundTexture]] = {}
_WORLD_CACHE_MAX = 2  # large worlds hold big ground textures: keep the two most recent


def _scenario_world(sc: Scenario) -> tuple[World, GroundTexture]:
    """A scenario's own world, built and textured once per world (scenarios of one world share it)."""
    key = sc.world
    if key in _WORLD_CACHE:
        _WORLD_CACHE[key] = _WORLD_CACHE.pop(key)  # most recent last
    else:
        w = sc.world()
        _WORLD_CACHE[key] = (w, build_ground_texture(w))
        while len(_WORLD_CACHE) > _WORLD_CACHE_MAX:
            _WORLD_CACHE.pop(next(iter(_WORLD_CACHE)))
    return _WORLD_CACHE[key]


@dataclass
class SimOutputs:
    t_ms: float
    drone_frame: FramePacket | None = None
    phone: PhonePacket | None = None
    phone_gt: list[dict] | None = None  # per phone vision box (evaluation only; never sent to the pipeline)
    telemetry: TelemetryPacket | None = None
    actions: list[ScriptedAction] = field(default_factory=list)
    restarted: bool = False
    dropped_frame: bool = False


class SimulationDriver:
    WORLD_HZ = 30.0

    def __init__(self, cfg: Config, faults: FaultInjector, scenario_id: str | None = None, seed: int = 0) -> None:
        self.cfg = cfg
        self.faults = faults
        self.seed = seed
        mk = cfg.calibration.markers
        key = (tuple(sorted((int(k), tuple(v)) for k, v in mk.layout.items())), float(mk.size_m),
               tuple(tuple(p) for p in cfg.autonomy.geofence))
        self.world, self.texture = _cached_texture(key)
        self._field = (self.world, self.texture, SceneRenderer(self.world, self.texture))
        self._base_world = self.world
        self.renderer = self._field[2]
        self._base_renderer = self.renderer
        dc = cfg.sources.drone
        self.drone_intr = Intrinsics.from_hfov(dc.width, dc.height, dc.hfov_deg)
        self.rng = np.random.default_rng(seed + 101)
        self._autonomy_defaults = cfg.autonomy.model_dump()  # a scenario's overrides are undone on the next load
        self._locks_defaults = cfg.locks.model_dump()
        self.load(scenario_id or cfg.session.scenario)

    # ---------------------------------------------------------------------------------------------------------
    def load(self, scenario_id: str) -> None:
        sc: Scenario = get_scenario(scenario_id)
        if sc.world is not None:  # a world of its own (pursuit, search and rescue, flood, fire)
            base_world, texture = _scenario_world(sc)
            base_renderer = SceneRenderer(base_world, texture)
        else:
            base_world, texture, base_renderer = self._field
        self._base_world, self.texture, self._base_renderer = base_world, texture, base_renderer
        # the autonomy settings are the configured ones, then the scenario's (the low-altitude stack flies lower
        # and closer), then the loaded world's geofence (the training field's comes from the config)
        for k, v in self._autonomy_defaults.items():
            setattr(self.cfg.autonomy, k, v)
        for k, v in (sc.autonomy or {}).items():
            setattr(self.cfg.autonomy, k, v)
        for k, v in {**self._locks_defaults, **(sc.locks or {})}.items():
            setattr(self.cfg.locks, k, v)
        if self._base_world.geofence:
            self.cfg.autonomy.geofence = [tuple(p) for p in self._base_world.geofence]
        self._reset_scene(sc)
        self.loops = 0

    def _reset_scene(self, sc: Scenario | None = None) -> None:
        sc = sc or self.scenario
        if sc.extra_boxes:  # scenario-only objects on a copy of the shared world
            world = copy.copy(self._base_world)
            world.boxes = list(self._base_world.boxes) + list(sc.extra_boxes)
            renderer = SceneRenderer(world, self.texture)
        else:
            world, renderer = self._base_world, self._base_renderer
        self.world, self.renderer = world, renderer
        from .survey import CameraSurvey
        self.survey = CameraSurvey(world)
        if getattr(self, "rescue", None) is not None:
            self.rescue.reset()
        self.scenario = sc
        self.actors = [Actor(spec, self.seed) for spec in sc.actors]
        d = sc.drone
        self.drone = SimDrone(d.x, d.y, d.alt, d.heading, d.gimbal_pitch, seed=self.seed + 5)
        self._pilot_i = 1  # next waypoint of a scripted manual flight
        if sc.dynamics == "mujoco" or sc.stack == "jev-x2":
            try:
                from .mujoco_drone import MujocoDrone

                self.drone = MujocoDrone(d.x, d.y, d.alt, d.heading, d.gimbal_pitch, seed=self.seed + 5)
            except Exception as e:  # noqa: BLE001 - MuJoCo is optional: fall back to the kinematic model, visibly
                log.warning("MuJoCo drone unavailable (%s): using the kinematic model", e)
        self.bridge = SimBridge(self.drone, self.cfg.autonomy.command_ttl_ms, self.cfg.autonomy.link_timeout_s * 1000.0,
                                seed=self.seed + 9)
        self.phone = PhoneSim(self.cfg, self.world, self.renderer, holder_id="responder", seed=self.seed + 3)
        self.t_s = 0.0
        self.frame_id = 0
        self._next_drone = 0.0
        self._next_phone = 0.02
        self._next_tel = 0.01
        self._delayed: deque[tuple[float, FramePacket]] = deque()
        self._done_actions: set[int] = set()
        self._gimbal_base: float | None = None
        self.last_gt: dict[str, Any] = {}

    @property
    def t_ms(self) -> float:
        return self.t_s * 1000.0

    # ---------------------------------------------------------------------------------------------------------
    def _scripted_faults(self) -> None:
        active: dict[str, Any] = {}
        for fw in self.scenario.faults:
            if fw.t0 <= self.t_s < fw.t1:
                active[fw.key] = fw.value
        if self.scenario.hide_markers:
            active["markers_hidden"] = True
        self.faults.set_scripted(active)

    def step(self, dt: float, render: bool = True) -> SimOutputs:
        """Advance by ``dt`` seconds. ``render=False`` skips image synthesis (used when catching up)."""
        out = SimOutputs(t_ms=0.0)
        if self.scenario.loop and self.t_s >= self.scenario.duration_s:
            self._reset_scene()
            self.loops += 1
            out.restarted = True
        self.t_s += dt
        t_ms = self.t_ms
        out.t_ms = t_ms
        self._scripted_faults()
        f = self.faults.active()

        for a in self.actors:
            a.update(dt)
        if getattr(self, "rescue", None) is not None:
            self.rescue.step_sim(dt)
        # gimbal pan fault: an unexpected slow gimbal movement the pipeline must notice
        if f.get("gimbal_pan"):
            if self._gimbal_base is None:
                self._gimbal_base = self.drone.gimbal_target
            self.drone.gimbal_target = float(np.clip(self._gimbal_base + 14.0 * math.sin(self.t_s * 0.8), -90, -20))
        elif self._gimbal_base is not None:
            self.drone.gimbal_target = self._gimbal_base
            self._gimbal_base = None
        self.bridge.link_up = not f.get("bridge_link_loss", False)
        self.bridge.step(dt, t_ms, wind_gusts=bool(f.get("wind_gust")))
        self._fly_pilot_path(dt)
        climb = self.scenario.drone.climb  # scripted pilot throttle (only while the pilot has the sticks)
        if climb and not self.drone.vstick_enabled and self.t_s >= climb[2] and self.drone.pos[2] < climb[0]:
            self.drone.pos[2] = min(climb[0], self.drone.pos[2] + climb[1] * dt)

        # scripted operator actions
        for i, act in enumerate(self.scenario.actions):
            if i not in self._done_actions and self.t_s >= act.t:
                self._done_actions.add(i)
                out.actions.append(act)

        # --- drone video -----------------------------------------------------------------------------------
        dc = self.cfg.sources.drone
        if self.t_s >= self._next_drone:
            self._next_drone += 1.0 / dc.target_fps
            if self._next_drone < self.t_s:
                self._next_drone = self.t_s + 1.0 / dc.target_fps
            if render and not f.get("drone_video_drop"):
                drop_p = float(f.get("frame_drop_pct") or 0.0)
                if drop_p > 0 and self.rng.random() < drop_p:
                    out.dropped_frame = True
                else:
                    pkt = self._render_drone(t_ms, f)
                    lat = float(f.get("video_latency_ms") or 0.0)
                    if lat > 0:
                        jitter = float(self.rng.normal(0, lat * 0.08))
                        self._delayed.append((t_ms + lat + jitter, pkt))
                    else:
                        out.drone_frame = pkt
        while self._delayed and self._delayed[0][0] <= t_ms:
            _, pkt = self._delayed.popleft()
            pkt.receive_ts_ms = t_ms
            out.drone_frame = pkt  # newest due frame wins (latest-frame queue)

        # --- phone -----------------------------------------------------------------------------------------
        if self.t_s >= self._next_phone:
            self._next_phone += 1.0 / self.cfg.sources.phone.target_hz
            if not f.get("phone_drop"):
                pf = self.phone.step(t_ms, self.actors, f, want_rgb=render)
                if pf is not None:
                    out.phone = PhonePacket(pf.observation, t_ms, "SYNTHETIC", pf.rgb)
                    out.phone_gt = pf.box_gt

        # --- telemetry -------------------------------------------------------------------------------------
        if self.t_s >= self._next_tel:
            self._next_tel += 0.1
            if not f.get("telemetry_drop"):
                tel = self.bridge.telemetry(t_ms, self.cfg.world.geo_reference, self.cfg.world.north_offset_deg,
                                            gps_scale=4.0 if f.get("gps_degraded") else 1.0)
                out.telemetry = TelemetryPacket(tel, t_ms, "SYNTHETIC")
        return out

    def _fly_pilot_path(self, dt: float) -> None:
        """Scripted manual flight along waypoints (x, y, alt, speed): the pilot has the sticks, nothing is automated."""
        path = self.scenario.pilot_path
        if not path or self.drone.vstick_enabled or self._pilot_i >= len(path):
            return
        x, y, alt, speed = path[self._pilot_i]
        p = self.drone.pos
        d = np.array([x - p[0], y - p[1], alt - p[2]])
        dist = float(np.linalg.norm(d))
        step = speed * dt
        if dist <= max(step, 0.05):
            p[0], p[1], p[2] = x, y, alt
            self._pilot_i += 1
        else:
            p[0], p[1], p[2] = p[0] + d[0] / dist * step, p[1] + d[1] / dist * step, p[2] + d[2] / dist * step

    def _hazard_overlay(self, cam: PinholeCamera, img: np.ndarray, t_s: float,
                        people: list[tuple[float, float, float, float]] | None = None) -> dict[str, Any] | None:
        """Water, fire and smoke drawn per pixel from the scenario's hazard models (screening models)."""
        hz = self.scenario.hazards
        if hz is None:
            return None
        H, W = img.shape[:2]
        step = 4
        us, vs = np.meshgrid(np.arange(0, W, step) + step / 2, np.arange(0, H, step) + step / 2)
        pts, hit = cam.pixel_to_ground(np.column_stack([us.ravel(), vs.ravel()]), max_range_m=400.0)
        color, alpha = hz.overlay(pts[:, 0], pts[:, 1], hit, t_s)
        if alpha is None or not alpha.any():
            return None
        color = cv2.resize(color.reshape(us.shape + (3,)).astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
        alpha = cv2.resize(alpha.reshape(us.shape).astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
        if not getattr(hz, "covers_people", True):  # water lies on the ground: roofs, crowns and people stay dry
            for x0, y0, x1, y1 in people or []:
                alpha[max(0, int(y0)):min(H, int(y1 - 0.2 * (y1 - y0)) + 1), max(0, int(x0)):min(W, int(x1) + 1)] = 0.0
            dry = np.zeros((H, W), np.uint8)
            for bx in self.renderer._boxes():  # noqa: SLF001 - only what can be in view
                uv, z = cam.project(bx.corners())
                if (z > 0.5).all():
                    cv2.fillConvexPoly(dry, cv2.convexHull(uv.astype(np.float32)).astype(np.int32), 1)
            for tr in self.renderer._trees():  # noqa: SLF001
                ring = np.array([[tr.xy[0] + tr.canopy_r * math.cos(a), tr.xy[1] + tr.canopy_r * math.sin(a), tr.canopy_z]
                                 for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)])
                uv, z = cam.project(ring)
                if (z > 0.5).all():
                    cv2.fillConvexPoly(dry, cv2.convexHull(uv.astype(np.float32)).astype(np.int32), 1)
            alpha[dry.astype(bool)] = 0.0
        alpha = alpha[..., None]
        img[:] = np.clip(img.astype(np.float32) * (1 - alpha) + color * alpha, 0, 255).astype(np.uint8)
        return {"label": hz.label}

    # ---------------------------------------------------------------------------------------------------------
    def drone_camera(self, jitter_deg: float = 0.25) -> PinholeCamera:
        return PinholeCamera(self.drone_intr, self.drone.camera_pose(jitter_deg))

    def _render_drone(self, t_ms: float, f: dict[str, Any]) -> FramePacket:
        # an ideal camera on a real airframe: a little residual gimbal wobble, otherwise a clean feed (no sensor noise,
        # no vignette, no compression) unless a scenario's fault window degrades it on purpose
        cam = self.drone_camera(1.6 if f.get("wobble") else 0.22)
        self.survey.update(self.t_s, cam)
        fx = RenderEffects(
            blur_px=9.0 if f.get("blur") else 0.0,
            brightness=0.38 if f.get("low_light") else 1.0,
            noise_sigma=7.0 if f.get("low_light") else 0.0,
            saturation=0.65 if f.get("low_light") else 1.0,
            jpeg_quality=None,
        )
        res = self.renderer.render(cam, self.actors, fx, ground_level=False, with_gt=True)
        img = res.image
        hazards = self._hazard_overlay(cam, img, self.t_s,
                                       [g.box_xyxy for g in res.gt.values() if g.box_xyxy and g.visibility > 0])
        if f.get("markers_hidden"):
            self._cover_markers(cam, img)
        self.frame_id += 1
        gt = {
            "t_ms": t_ms,
            "actors": {k: vars(v) for k, v in res.gt.items()},
            "states": {a.id: a.state_dict() for a in self.actors},
            "camera": {"position": cam.pose.position.tolist(), "heading": cam.pose.heading_deg,
                       "pitch": cam.pose.pitch_deg, "roll": cam.pose.roll_deg},
            "drone_xy": self.drone.true_xy().tolist(),
            "hazards": hazards,
        }
        self.last_gt = gt
        return FramePacket("sim-drone", self.frame_id, img, t_ms, t_ms, "SYNTHETIC", "exact",
                           meta={"yaw_rate_dps": self.drone.yaw_rate}, gt=gt)

    def _cover_markers(self, cam: PinholeCamera, img: np.ndarray) -> None:
        for m in self.world.markers:
            corners = marker_world_corners(m.xy, m.size_m * 1.5)
            uv, z = cam.project(np.column_stack([corners, np.zeros(4)]))
            if (z > 0.1).all():
                cv2.fillPoly(img, [np.round(uv).astype(np.int32)], (52, 98, 60), cv2.LINE_AA)

    # ---------------------------------------------------------------------------------------------------------
    def actor(self, actor_id: str) -> Actor | None:
        return next((a for a in self.actors if a.id == actor_id), None)

    def gt_states(self) -> dict[str, dict]:
        return {a.id: a.state_dict() for a in self.actors}

    def control_actor(self, actor_id: str, action: str, **kw: Any) -> dict:
        """Operator control of a simulated person (the demo page). Simulator only; logged by the caller."""
        a = self.actor(actor_id)
        if a is None:
            raise KeyError(f"no such person {actor_id}")
        a.control(action, **kw)
        return a.state_dict()

    def crew_problem(self) -> Any:
        """The scenario's crew-routing problem (flood, fire), built once per load; None when it has none."""
        sc = self.scenario
        if getattr(self, "_crew_for", None) is not sc:
            self._crew_for = sc
            self._crew = sc.crew_problem() if sc.crew_problem is not None else None
        return self._crew

    def map_data(self) -> dict | None:
        """The loaded world's street graph as map data for pursuit routing, with cover from above along each street
        (trees, carports, covered walkways: the scenario's own objects included). None without a street map."""
        w = self.world
        if getattr(w, "streets", None) is None:
            return None
        key = (id(w), len(w.boxes), len(w.trees))
        if getattr(self, "_map_key", None) != key:
            from .world import street_cover

            self._map_key = key
            self._map = {**w.streets, "cover": street_cover(w.streets, w.boxes, w.trees)}
        return self._map

    def status(self) -> dict[str, Any]:
        return {"scenario": self.scenario.id, "scenario_name": self.scenario.name, "t_s": round(self.t_s, 2),
                "duration_s": self.scenario.duration_s, "loops": self.loops,
                "drone_model": getattr(self.drone, "model_name", "kinematic Mini 2 stand-in"), "stack": self.scenario.stack}

    def obstacle_scene(self) -> dict[str, Any]:
        """What a forward depth camera would see of the static world at the aircraft's height (the jev-drone
        stack's symbolic scene). Simulator geometry: never the people."""
        from ..autonomy.reflex import scene_sectors

        p = self.drone.pos
        return scene_sectors(self.renderer._boxes(), self.renderer._trees(),  # noqa: SLF001 - the culled sets
                             (float(p[0]), float(p[1]), float(p[2])), float(self.drone.heading))

    def orthophoto(self, res_m: float = 0.1) -> tuple[np.ndarray, tuple[float, float, float, float]]:
        small = self.texture.downsampled(res_m)
        return small.image, self.world.bounds
