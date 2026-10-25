"""Autonomy controller: IBVS follow, reacquire state machine with bounded search primitives, safety supervisor,
virtual-stick output at 20 Hz.

Modes: MANUAL (pilot), FOLLOW, HOLD, RTL, ESTOP. FSM inside FOLLOW: TRACKING -> COAST (lost > 0.7 s) ->
REACQUIRE (> 2 s) -> HOLD (> 20 s or geofence). Decision models only choose among bounded primitives (task D1);
they never emit velocities. Commands reach a real aircraft only if ``autonomy.allow_real_flight`` is true;
otherwise they are computed and displayed as ADVISORY.
"""

from __future__ import annotations

import math
import secrets
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from ..config import AutonomyCfg
from ..geometry.camera import heading_from_vector, wrap_deg
from ..schemas import AutonomyState, BridgeCommand, SafetyStatus, VStickCommand
from .pid import PID
from .reflex import reflex
from .safety import DroneSafetyState, SafetySupervisor


class BridgeLink(Protocol):
    is_sim: bool

    def connected(self, now_ms: float) -> bool: ...

    def send(self, cmd: BridgeCommand) -> None: ...


@dataclass
class AutonomyInputs:
    now_ms: float
    subject_state: str
    subject_box: tuple[float, float, float, float] | None
    image_size: tuple[int, int]
    hfov_deg: float
    vfov_deg: float
    subject_xy: tuple[float, float] | None
    subject_vel: tuple[float, float] | None
    last_seen_xy: tuple[float, float] | None
    lost_s: float | None
    drone: DroneSafetyState
    gimbal_pitch_deg: float | None
    people_xy: list[tuple[float, float]] = field(default_factory=list)
    calibration_valid: bool = False
    occluder_near_last_seen: bool = False
    footprint: list[tuple[float, float]] | None = None
    responder_xy: tuple[float, float] | None = None
    subject_verified: bool = True  # False after the lock re-bound to a track nobody confirmed
    video_fresh: bool = True
    subject_track_id: str | None = None
    subject_quality_ok: bool = True
    subject_quality_reasons: tuple[str, ...] = ()
    subject_frame_id: int | None = None


@dataclass
class DispatchPlan:
    """The flight part of a dispatch, computed by code: the pad, the altitude, and the waypoints (the way to the
    search area, then the legs that cover it)."""

    description: str
    pad_xy: tuple[float, float]
    waypoints: list[tuple[float, float]]
    altitude_m: float = 28.0
    speed_mps: float = 6.0
    gimbal_deg: float = -72.0
    phase: str = "TAKEOFF"  # TAKEOFF | TRANSIT | SEARCH | COVERED
    wp_i: int = 0
    transit_legs: int = 1  # the first waypoints are the way to the area, the rest are its legs


class AutonomyController:
    def __init__(self, cfg: AutonomyCfg, bridge: BridgeLink | None) -> None:
        self.cfg = cfg
        self.bridge = bridge
        self.safety = SafetySupervisor(cfg)
        self.pid_yaw = PID(1.5, 0.05, 0.12, limit=cfg.max_yaw_rate_dps, i_limit=10)
        self.pid_fwd = PID(0.5, 0.02, 0.15, limit=cfg.reacquire_max_speed_mps, i_limit=1.0)
        self.pid_alt = PID(0.6, 0.0, 0.0, limit=cfg.max_vertical_mps)
        self.audit: deque[dict] = deque(maxlen=300)
        self.obstacle_sensor: Any = None  # callable() -> scene dict (see reflex.scene_sectors); None: no reflex
        self.reset()

    def reset(self) -> None:
        self.mode = "MANUAL"
        self.fsm = "IDLE"
        self.primitive: str | None = None
        self.primitive_source = "none"
        self.primitive_until_ms = -1e12
        self.last_tick_ms: float | None = None
        self.last_send_ms = -1e12
        self.last_ping_ms = -1e12
        self.seq = 0
        self.control_epoch = getattr(self, "control_epoch", 0) + 1
        self.last_cmd: VStickCommand | None = None
        self.gimbal_cmd: float | None = None
        self._gimbal_sent: float | None = None
        self._gimbal_sent_ms = -1e12
        self._orbit_angle: float | None = None
        self._climb_base: float | None = None
        self.route: list[np.ndarray] = []
        self.route_i = 0
        self.route_label: str | None = None
        self.route_look_at: np.ndarray | None = None  # where the camera points once the route's viewpoint is reached
        self.plan: DispatchPlan | None = None
        # the low-altitude (jev-drone) stack: a scene sensor, the reflex and a committed maneuver from D6
        self.scene: dict[str, Any] | None = None
        self.reflex_reason: str | None = None
        self.maneuver: tuple[str, float, str] | None = None  # (name, until_ms, source)
        self._verify_noted = False
        self.safety_status = SafetyStatus()
        self.note: str | None = None
        self.keyboard_session: str | None = None
        self.keyboard_until_ms = -1e12
        self.keyboard_seq = -1
        self.keyboard_axes = (0.0, 0.0, 0.0, 0.0)
        self.visual_reference_height: float | None = None
        self.visual_height: float | None = None
        self.visual_samples = 0
        self.visual_track_id: str | None = None
        self.visual_frame_id: int | None = None
        for p in (self.pid_yaw, self.pid_fwd, self.pid_alt):
            p.reset()

    # ---------------------------------------------------------------------------------------------------------
    @property
    def target_is_sim(self) -> bool:
        return bool(self.bridge is not None and getattr(self.bridge, "is_sim", False))

    @property
    def commands_enabled(self) -> bool:
        return (self.bridge is not None and (self.target_is_sim or self.cfg.allow_real_flight) and self.cfg.enabled
                and getattr(self.bridge, "control_capable", True)
                and not getattr(self.bridge, "control_inhibited", False))

    def clear_plan(self) -> None:
        self.primitive = None
        self.primitive_source = "none"
        self.primitive_until_ms = -1e12
        self.route = []
        self.route_i = 0
        self.route_label = None
        self.last_cmd = None
        self.gimbal_cmd = None

    def _send(self, cmd: BridgeCommand) -> None:
        stop = cmd.type in ("estop", "hover") or (cmd.type == "mode" and cmd.payload.get("vstick") is False)
        if self.bridge is not None and (self.commands_enabled or stop):
            self.bridge.send(cmd)

    def _log(self, now_ms: float, kind: str, text: str) -> None:
        self.audit.append({"ts_ms": now_ms, "kind": kind, "text": text})

    def set_mode(self, mode: str, now_ms: float, source: str = "operator", *, emit_command: bool = True) -> str:
        mode = mode.upper()
        if mode == "DISPATCH" and not self.target_is_sim:
            raise ValueError("dispatch is supported only by the simulator")
        if mode not in ("MANUAL", "FOLLOW", "HOLD", "RTL", "ESTOP", "DISPATCH", "GOTO"):
            raise ValueError(f"unknown mode {mode}")
        if self.mode == "ESTOP" and mode not in ("MANUAL", "ESTOP"):
            raise ValueError("E-STOP latched: switch to MANUAL first")
        prev = self.mode
        self.keyboard_session = None
        self.keyboard_axes = (0.0, 0.0, 0.0, 0.0)
        self.visual_reference_height = self.visual_height = None
        self.visual_samples = 0
        self.visual_track_id = None
        self.visual_frame_id = None
        self.control_epoch += 1
        self.clear_plan()
        self.mode = mode
        self.seq += 1
        if mode in ("FOLLOW", "HOLD", "DISPATCH", "GOTO"):
            self._send(BridgeCommand(type="mode", seq=self.seq, ts_ms=now_ms,
                                    payload={"vstick": True, "motion_profile": "full" if self.cfg.motion_profile == "visual_follow" else self.cfg.motion_profile}))
            for p in (self.pid_yaw, self.pid_fwd, self.pid_alt):
                p.reset()
        elif mode == "MANUAL":
            if emit_command:
                self._send(BridgeCommand(type="mode", seq=self.seq, ts_ms=now_ms, payload={"vstick": False}))
            self.fsm = "IDLE"
        elif mode == "RTL":
            self._send(BridgeCommand(type="rtl", seq=self.seq, ts_ms=now_ms))
            self.fsm = "IDLE"
        elif mode == "ESTOP":
            self._send(BridgeCommand(type="estop", seq=self.seq, ts_ms=now_ms))
            self.fsm = "IDLE"
        adv = "" if self.commands_enabled else " (ADVISORY: real-flight commands disabled)"
        self._log(now_ms, "mode", f"{prev} -> {mode} by {source}{adv}")
        return self.mode

    def start_keyboard(self, now_ms: float) -> str:
        if self.cfg.motion_profile not in ("camera_only", "visual_follow") or not self.commands_enabled:
            raise ValueError("keyboard control requires a supported profile and an enabled, re-armed bridge")
        if self.keyboard_session is not None:
            raise ValueError("keyboard control is already owned by another session")
        self.set_mode("HOLD", now_ms, "dashboard keyboard")
        self.keyboard_session = secrets.token_urlsafe(24)
        self.keyboard_until_ms = now_ms + 300
        self.keyboard_seq = -1
        return self.keyboard_session

    def keyboard_input(self, session: str, seq: int, yaw: float, tilt: float, now_ms: float, forward: float = 0, right: float = 0) -> None:
        if (session != self.keyboard_session or self.mode != "HOLD" or now_ms >= self.keyboard_until_ms
                or not self.commands_enabled or seq <= self.keyboard_seq):
            raise ValueError("keyboard session expired, superseded or inhibited; enable it again explicitly")
        if not all(math.isfinite(v) and -1 <= v <= 1 for v in (yaw, tilt, forward, right)):
            raise ValueError("keyboard axes must be finite values between -1 and 1")
        self.keyboard_seq = seq
        if self.cfg.motion_profile == "camera_only" and (forward or right):
            raise ValueError("enable Movement + camera before requesting translation")
        self.keyboard_axes = (yaw, tilt, forward, right)
        self.keyboard_until_ms = now_ms + 300

    def stop_keyboard(self, session: str, now_ms: float) -> None:
        if session == self.keyboard_session:
            self.set_mode("MANUAL", now_ms, "keyboard released")

    def set_primitive(self, primitive: str, source: str, now_ms: float, hold_ms: float = 2000.0) -> None:
        if primitive == "other":
            return
        if primitive != self.primitive:
            self._orbit_angle = None
            self._climb_base = None
            self._log(now_ms, "primitive", f"search primitive {primitive} ({source})")
        self.primitive = primitive
        self.primitive_source = source
        self.primitive_until_ms = now_ms + hold_ms

    def set_route(self, route_id: str, waypoints: list, label: str, source: str, now_ms: float,
                  hold_ms: float = 8000.0, look_at: tuple[float, float] | None = None) -> None:
        """Fly a bounded pursuit route (simulator; an advisory for a real aircraft): waypoints in turn at the reacquire
        speed cap, then hover at the last one with the camera on ``look_at`` (a viewpoint), or orbit the last one.
        The safety supervisor still clamps every command."""
        self.route = [np.array(w, float) for w in waypoints]
        self.route_i = 0
        self.route_label = label
        self.route_look_at = np.array(look_at, float) if look_at is not None else None
        self._orbit_angle = None
        self.set_primitive(f"route:{route_id}", source, now_ms, hold_ms)

    def set_maneuver(self, name: str, source: str, now_ms: float, commit_ms: float = 1400.0) -> None:
        """Commit to a D6 maneuver for a short window instead of chattering; the reflex can still override it."""
        if name in ("other", "hold_course") or self.obstacle_sensor is None:
            return
        if self.maneuver is None or self.maneuver[0] != name:
            self._log(now_ms, "maneuver", f"{name} for {commit_ms / 1000:.1f} s ({source})")
            self._climb_base = None
        self.maneuver = (name, now_ms + commit_ms, source)

    def _maneuver_cmd(self, name: str, fwd: float, right: float, up: float, yaw: float, inp: AutonomyInputs,
                      dt: float) -> tuple[float, float, float, float]:
        d = inp.drone
        if name == "hold_position":
            return 0.0, 0.0, self._alt_hold(inp, dt, d.alt_m), 0.0
        if name == "sidestep_left":
            return min(fwd, 1.0), -2.0, up, yaw
        if name == "sidestep_right":
            return min(fwd, 1.0), 2.0, up, yaw
        if name == "climb":
            if self._climb_base is None:
                self._climb_base = d.alt_m or self.cfg.altitude_m
            tgt = min(self.cfg.max_altitude_m, self._climb_base + 4.0)
            return min(fwd, 1.5), right, self._alt_hold(inp, dt, tgt), yaw
        return fwd, right, up, yaw

    def start_dispatch(self, plan: "DispatchPlan", now_ms: float, source: str = "operator") -> None:
        """Take off from the pad, fly to the search area and fly its legs; the pipeline matches people seen on the
        way against the description and hands over to FOLLOW when one matches."""
        self.plan = plan
        self.route, self.route_label = [], None
        self.set_mode("DISPATCH", now_ms, source)
        self._log(now_ms, "dispatch", f"dispatch: look for '{plan.description}', {len(plan.waypoints)} waypoints "
                                      f"at {plan.altitude_m:.0f} m ({source})")

    def start_goto(self, waypoints: list, look_at: tuple[float, float], label: str, now_ms: float,
                   source: str = "operator") -> None:
        """Fly to a place and look at it (an operator's GOTO): the waypoints in turn at the reacquire speed cap,
        then hover at the last one with the camera on ``look_at``. Bounded velocity setpoints; the safety supervisor
        clamps every command."""
        self.set_mode("GOTO", now_ms, source)
        self.plan = None
        self.route = [np.array(w, float) for w in waypoints]
        self.route_i = 0
        self.route_label = label
        self.route_look_at = np.array(look_at, float)
        self.primitive, self.primitive_source = "route:goto", source
        self._log(now_ms, "goto", f"go to {label} ({source})")

    def resume_search(self, now_ms: float, source: str) -> None:
        """Back to the search legs after a rejected match (the operator released the lock)."""
        if self.plan is not None:
            self.plan.phase = "SEARCH" if self.plan.wp_i < len(self.plan.waypoints) else "COVERED"
            self.set_mode("DISPATCH", now_ms, source)

    def _dispatch_tick(self, inp: AutonomyInputs, dt: float) -> tuple[float, float, float, float]:
        plan = self.plan
        d = inp.drone
        if plan is None or d.xy is None or d.heading_deg is None:
            self.fsm = "IDLE"
            return 0.0, 0.0, 0.0, 0.0
        h = math.radians(d.heading_deg)
        fv = np.array([math.sin(h), math.cos(h)])
        rv = np.array([math.cos(h), -math.sin(h)])
        pos = np.array(d.xy)
        up = self._alt_hold(inp, dt, plan.altitude_m)
        self.gimbal_cmd = plan.gimbal_deg
        if plan.phase == "TAKEOFF":
            if d.alt_m is None or d.alt_m < plan.altitude_m - 1.5:
                self.fsm = "TAKEOFF"
                return 0.0, 0.0, up, 0.0  # straight up, no horizontal motion until it is at height
            plan.phase = "TRANSIT"
            self._log(inp.now_ms, "dispatch", f"airborne at {d.alt_m:.0f} m: flying to the search area")
        if plan.wp_i >= len(plan.waypoints):
            self.fsm = "COVERED"
            if plan.phase != "COVERED":
                plan.phase = "COVERED"
                self._log(inp.now_ms, "dispatch", "search area covered: nobody matching the description; orbiting")
            c = np.array(plan.waypoints[-1]) if plan.waypoints else pos
            inp2 = AutonomyInputs(**{**inp.__dict__, "last_seen_xy": (float(c[0]), float(c[1])), "subject_xy": None})
            self.primitive = "orbit_last_seen"
            fwd, right, _, yaw = self._primitive(inp2, dt)
            return fwd, right, up, yaw
        tgt = np.array(plan.waypoints[plan.wp_i], float)
        to = tgt - pos
        dist = float(np.linalg.norm(to))
        if dist < 3.5:
            plan.wp_i += 1
            if plan.phase == "TRANSIT":
                plan.phase = "SEARCH"
                self._log(inp.now_ms, "dispatch", "over the search area: flying the legs")
            return 0.0, 0.0, up, 0.0
        self.fsm = "TRANSIT" if plan.phase == "TRANSIT" else "SEARCH"
        v = to / max(dist, 1e-6) * min(plan.speed_mps, 0.9 * dist + 1.0)
        return float(np.dot(v, fv)), float(np.dot(v, rv)), up, self._yaw_to(d.xy, (float(tgt[0]), float(tgt[1])), d.heading_deg)

    # ---------------------------------------------------------------------------------------------------------
    def d1_facts(self, inp: AutonomyInputs) -> dict[str, Any] | None:
        if self.mode != "FOLLOW" or self.fsm != "REACQUIRE":
            return None
        d = inp.drone
        facts: dict[str, Any] = {
            "time_since_loss_s": round(inp.lost_s or 0.0, 1),
            "subject_last_speed_mps": round(math.hypot(*inp.subject_vel), 2) if inp.subject_vel else None,
            "subject_last_heading_deg": round(heading_from_vector(*inp.subject_vel), 0) if inp.subject_vel and math.hypot(*inp.subject_vel) > 0.3 else None,
            "drone_alt_m": round(d.alt_m, 1) if d.alt_m is not None else None,
            "battery_pct": round(d.battery_pct, 0) if d.battery_pct is not None else None,
            "geofence_margin_m": self.safety_status.geofence_margin_m,
            "occluder_near_last_seen": inp.occluder_near_last_seen,
        }
        if inp.subject_xy and d.xy and d.heading_deg is not None:
            dx, dy = inp.subject_xy[0] - d.xy[0], inp.subject_xy[1] - d.xy[1]
            facts["prediction_bearing_rel_drone_deg"] = round(wrap_deg(heading_from_vector(dx, dy) - d.heading_deg), 0)
            facts["prediction_distance_m"] = round(math.hypot(dx, dy), 1)
        if inp.footprint and inp.subject_xy:
            facts["prediction_in_footprint"] = _in_poly(inp.footprint, *inp.subject_xy)
        if inp.responder_xy and inp.subject_xy:
            facts["responder_range_m"] = round(math.dist(inp.responder_xy, inp.subject_xy), 1)
        return facts

    def tick(self, inp: AutonomyInputs) -> tuple[AutonomyState, list[dict]]:
        now = inp.now_ms
        if getattr(self.bridge, "control_inhibited", False) and self.mode not in ("MANUAL", "ESTOP"):
            self.set_mode("MANUAL", now, "bridge control inhibited: pilot in control")
        dt = 0.05 if self.last_tick_ms is None else max(1e-3, (now - self.last_tick_ms) / 1000.0)
        self.last_tick_ms = now
        events: list[dict] = []
        link_ok = self.bridge.connected(now) if self.bridge is not None else False
        inp.drone.link_ok = link_ok
        if self.keyboard_session and (now >= self.keyboard_until_ms or not link_ok or not inp.video_fresh):
            self.set_mode("MANUAL", now, "keyboard timeout or stale video/link")
        if self.mode in ("MANUAL", "ESTOP", "RTL") or self.bridge is None:
            if now - self.last_ping_ms > 500:
                self.last_ping_ms = now
                self._send(BridgeCommand(type="ping", ts_ms=now))
            self.fsm = "IDLE"
            self.safety_status = self.safety.apply(0, 0, 0, 0, inp.drone, [], False).status
            return self.state(), events
        if not link_ok:
            self.note = "bridge link down: bridge watchdog hovers, pilot regains control after 2 s"

        if self.cfg.motion_profile in ("camera_only", "visual_follow"):
            return self._camera_only_tick(inp, dt, link_ok)

        if not self.target_is_sim and (inp.subject_state != "LOCKED" or inp.subject_box is None
                                       or not inp.subject_verified or not inp.video_fresh or not link_ok
                                       or not inp.subject_quality_ok
                                       or inp.subject_xy is None or not inp.calibration_valid):
            # Physical full follow never flies a speculative search route or pursues an unverified re-binding.
            self.clear_plan()
            self.fsm = "VERIFY" if not inp.subject_verified else "HOLD"
            self.note = "Physical follow stopped: waiting for a visible, verified target and fresh sensors"
            self.safety_status = SafetyStatus(link_ok=link_ok, clamps=["target-loss hover: no translating search"])
            if now - self.last_send_ms >= 1000 / self.cfg.command_hz:
                self.last_send_ms = now
                self.seq += 1
                self.last_cmd = VStickCommand(seq=self.seq, ts_ms=now, forward_mps=0, right_mps=0, up_mps=0,
                                              yaw_rate_dps=0, ttl_ms=self.cfg.command_ttl_ms)
                self._send(BridgeCommand(type="vstick", seq=self.seq, ts_ms=now, payload=self.last_cmd.model_dump()))
            return self.state(), []

        fwd = right = up = yaw = 0.0
        reacq = False
        st = inp.subject_state
        if self.mode == "HOLD":
            self.fsm = "HOLD"
        elif self.mode == "DISPATCH":
            reacq = True  # the dispatch speed cap is the reacquire cap
            fwd, right, up, yaw = self._dispatch_tick(inp, dt)
        elif self.mode == "GOTO":
            reacq = True
            self.primitive = "route:goto"
            arrived = bool(self.route) and self.route_i == len(self.route) - 1 and \
                inp.drone.xy is not None and float(np.linalg.norm(self.route[-1] - np.array(inp.drone.xy))) < 4.0
            self.fsm = "HOLD" if arrived else "TRANSIT"
            fwd, right, up, yaw = self._primitive(inp, dt)
        elif st == "LOCKED" and inp.subject_box is not None and not inp.subject_verified:
            # the lock re-bound to a track nobody confirmed (a look-alike is possible): keep it in view, do not chase
            self.fsm = "VERIFY"
            W, H = inp.image_size
            cx = (inp.subject_box[0] + inp.subject_box[2]) / 2
            cy = (inp.subject_box[1] + inp.subject_box[3]) / 2
            yaw = self.pid_yaw.step((cx - W / 2) / (W / 2) * inp.hfov_deg / 2, dt)
            if inp.gimbal_pitch_deg is not None:  # camera only (yaw and gimbal): keep them in frame, do not move closer
                ey = (cy - 0.5 * H) / (H / 2) * inp.vfov_deg / 2
                self.gimbal_cmd = float(np.clip(inp.gimbal_pitch_deg - 0.5 * ey, -90.0, -20.0))
            up = self._alt_hold(inp, dt)
            if not self._verify_noted:
                self._verify_noted = True
                events.append({"kind": "autonomy_verify", "level": "warning",
                               "text": "Lock re-bound without confirmation: holding position until a person confirms "
                                       "or re-designates the subject"})
                self._log(now, "verify", "holding: lock re-bound without confirmation")
        elif st == "LOCKED" and inp.subject_box is not None:
            self.fsm = "TRACKING"
            self._verify_noted = False
            self.primitive, self.primitive_source = None, "none"
            self.route, self.route_label = [], None
            fwd, right, up, yaw = self._ibvs(inp, dt)
        elif st in ("COAST", "REACQUIRE", "UNCERTAIN", "LOCKED"):
            lost = inp.lost_s or 0.0
            reacq = True
            if lost < 2.0:
                # short occlusion: hold station, keep the camera on the predicted position
                self.fsm = "COAST"
                d = inp.drone
                if inp.subject_xy and d.xy and d.heading_deg is not None:
                    yaw = self._yaw_to(d.xy, inp.subject_xy, d.heading_deg) * 0.6
                up = self._alt_hold(inp, dt)
            else:
                self.fsm = "REACQUIRE"
                if self.primitive is None or now > self.primitive_until_ms:
                    self.route, self.route_label = [], None
                    from ..decisions.tasks import rules_d1
                    facts = self.d1_facts(inp) or {}
                    self.set_primitive(rules_d1(facts)["search_action"], "rules", now, 500.0)
                fwd, right, up, yaw = self._primitive(inp, dt)
        elif st == "HOLD":
            self.fsm = "HOLD"
        else:
            self.fsm = "IDLE"

        # the jev-drone stack: a committed maneuver from a judgment, then the reflex, which keeps the veto
        self.reflex_reason = None
        if self.obstacle_sensor is not None and self.mode in ("FOLLOW", "DISPATCH", "GOTO"):
            self.scene = self.obstacle_sensor()
            if self.maneuver is not None and now < self.maneuver[1]:
                fwd, right, up, yaw = self._maneuver_cmd(self.maneuver[0], fwd, right, up, yaw, inp, dt)
            elif self.maneuver is not None:
                self.maneuver = None
                self._climb_base = None
            r = reflex(fwd, right, up, yaw, self.scene)
            fwd, right, up, yaw = r.forward, r.right, r.up, r.yaw_rate
            self.reflex_reason = r.reason
        else:
            self.scene = None
        res = self.safety.apply(fwd, right, up, yaw, inp.drone, inp.people_xy, reacq)
        self.safety_status = res.status
        events.extend(res.events)
        if res.request_rtl and self.mode != "RTL":
            self.set_mode("RTL", now, "safety (battery)")
            return self.state(), events
        if now - self.last_send_ms >= 1000.0 / self.cfg.command_hz - 1e-6:
            self.last_send_ms = now
            self.seq += 1
            self.last_cmd = VStickCommand(seq=self.seq, ts_ms=now, forward_mps=round(res.forward, 3),
                                          right_mps=round(res.right, 3), up_mps=round(res.up, 3),
                                          yaw_rate_dps=round(res.yaw_rate, 2), ttl_ms=self.cfg.command_ttl_ms)
            self._send(BridgeCommand(type="vstick", seq=self.seq, ts_ms=now, payload=self.last_cmd.model_dump()))
        if self.gimbal_cmd is not None and (self._gimbal_sent is None or abs(self.gimbal_cmd - self._gimbal_sent) > 1.0) \
                and now - self._gimbal_sent_ms > 200:
            self._gimbal_sent = self.gimbal_cmd
            self._gimbal_sent_ms = now
            self._send(BridgeCommand(type="gimbal", ts_ms=now, payload={"pitch_deg": round(self.gimbal_cmd, 1)}))
        self.note = None if link_ok else self.note
        return self.state(), events

    # ---------------------------------------------------------------------------------------------------------
    def _camera_only_tick(self, inp: AutonomyInputs, dt: float, link_ok: bool) -> tuple[AutonomyState, list[dict]]:
        """Image centering and optional relative-size horizontal follow; never commands altitude or routes."""
        now = inp.now_ms
        self.route, self.route_label = [], None
        self.primitive, self.primitive_source = None, "none"
        yaw = forward = right = 0.0
        movement = self.cfg.motion_profile == "visual_follow"
        self.safety_status = SafetyStatus(link_ok=link_ok, clamps=[
            "visual follow: horizontal speed <= 0.5 m/s; vertical velocity zero" if movement else "camera-only: all translation disabled"])
        if inp.drone.battery_pct is not None and inp.drone.battery_pct < self.cfg.battery_rtl_pct:
            self.set_mode("MANUAL", now, "low battery: image-based control handed to pilot")
            self.note = "Low battery: pilot in control; no automatic translating RTL in image-based control"
            return self.state(), []
        visible = (self.mode == "FOLLOW" and inp.subject_state == "LOCKED" and inp.subject_box is not None
                   and inp.subject_verified and inp.video_fresh and link_ok)
        if self.keyboard_session:
            self.fsm = "HOLD"
            yaw_axis, tilt_axis, forward_axis, right_axis = self.keyboard_axes
            if movement and inp.video_fresh and link_ok:
                forward, right = forward_axis * self.cfg.visual_max_speed_mps, right_axis * self.cfg.visual_max_speed_mps
            yaw = yaw_axis * min(15.0, self.cfg.camera_yaw_max_dps, self.cfg.max_yaw_rate_dps)
            if tilt_axis and inp.gimbal_pitch_deg is not None:
                base = self.gimbal_cmd if self.gimbal_cmd is not None else inp.gimbal_pitch_deg
                self.gimbal_cmd = float(np.clip(base + tilt_axis * min(10.0, self.cfg.camera_gimbal_rate_dps)
                                               * min(dt, 0.1), -90.0, 0.0))
            else:
                self.gimbal_cmd = None
            self.note = "Manual keyboard: " + ("horizontal movement, yaw and camera pitch" if movement else "yaw and camera pitch only") + "; RC input cancels control"
        elif visible:
            self.fsm = "TRACKING"
            width, height = inp.image_size
            x0, y0, x1, y1 = inp.subject_box
            ex = ((x0 + x1) / 2 - width / 2) / (width / 2)
            ey = ((y0 + y1) / 2 - height / 2) / (height / 2)
            cap = min(15.0, self.cfg.camera_yaw_max_dps, self.cfg.max_yaw_rate_dps)
            if abs(ex) > 0.04:
                yaw = float(np.clip(0.8 * ex * inp.hfov_deg / 2, -cap, cap))
            if inp.gimbal_pitch_deg is not None:
                base = self.gimbal_cmd if self.gimbal_cmd is not None else inp.gimbal_pitch_deg
                delta = 0.0 if abs(ey) <= 0.04 else -0.5 * ey * inp.vfov_deg / 2
                step = min(10.0, self.cfg.camera_gimbal_rate_dps) * min(dt, 0.1)
                self.gimbal_cmd = float(np.clip(base + np.clip(delta, -step, step), -90.0, 0.0))
            if movement:
                if inp.subject_track_id != self.visual_track_id:
                    self.visual_reference_height = self.visual_height = None
                    self.visual_samples = 0
                    self.visual_track_id = inp.subject_track_id
                # Relative image framing, never a claim of metric range or obstacle clearance.
                # Do not translate from clipped, tiny or grossly changed detections.
                height_frac = (y1 - y0) / max(height, 1)
                unclipped = x0 > 0.02 * width and x1 < 0.98 * width and y0 > 0.02 * height and y1 < 0.98 * height
                trustworthy = (unclipped and 0.06 <= height_frac <= 0.8 and not inp.occluder_near_last_seen
                               and inp.subject_quality_ok)
                if trustworthy:
                    if self.visual_reference_height is None:
                        self.visual_reference_height = height_frac
                        self.visual_height = height_frac
                    ratio = height_frac / max(self.visual_height or height_frac, 0.001)
                    if 0.65 <= ratio <= 1.5:
                        if inp.subject_frame_id is None or inp.subject_frame_id != self.visual_frame_id:
                            self.visual_height = 0.75 * (self.visual_height or height_frac) + 0.25 * height_frac
                            self.visual_samples += 1
                            self.visual_frame_id = inp.subject_frame_id
                        if self.visual_samples >= 5:
                            right = 0.0 if abs(ex) < 0.08 else 0.4 * ex
                            # Only move along depth after the camera has almost centered the person.
                            error = math.log(self.visual_reference_height / self.visual_height)
                            if abs(ex) < 0.35 and abs(ey) < 0.35 and abs(error) > 0.08:
                                forward = float(np.clip(0.6 * error, -self.cfg.visual_max_speed_mps, self.cfg.visual_max_speed_mps))
                    else:
                        self.visual_samples = 0  # no approach based on a sudden posture/box change
                else:
                    self.visual_samples = 0
                self.note = ("Translation stopped: " + ", ".join(inp.subject_quality_reasons or ("uncertain bounding box",))
                             if not trustworthy else "Visual follow: center person with yaw, strafe and camera pitch; maintain starting image size; altitude stays pilot-controlled")
            else:
                self.note = "Camera follow: yaw and gimbal only; pilot controls position and altitude"
        else:
            self.gimbal_cmd = None
            self.visual_samples = 0
            self.pid_yaw.reset()
            self.fsm = "VERIFY" if inp.subject_state == "LOCKED" and not inp.subject_verified else "HOLD"
            self.note = "Target designation retained; rotation stopped until the verified target is visible again"
        horizontal = math.hypot(forward, right)
        if horizontal > self.cfg.visual_max_speed_mps:
            forward *= self.cfg.visual_max_speed_mps / horizontal
            right *= self.cfg.visual_max_speed_mps / horizontal
        if now - self.last_send_ms >= 1000.0 / self.cfg.command_hz - 1e-6:
            self.last_send_ms = now
            self.seq += 1
            self.last_cmd = VStickCommand(seq=self.seq, ts_ms=now, forward_mps=math.trunc(forward * 10000) / 10000, right_mps=math.trunc(right * 10000) / 10000, up_mps=0,
                                          yaw_rate_dps=round(yaw, 3), ttl_ms=self.cfg.command_ttl_ms)
            self._send(BridgeCommand(type="vstick", seq=self.seq, ts_ms=now, payload=self.last_cmd.model_dump()))
        if self.gimbal_cmd is not None and (self._gimbal_sent is None or abs(self.gimbal_cmd - self._gimbal_sent) >= 1.0) \
                and now - self._gimbal_sent_ms >= 200:
            self._gimbal_sent, self._gimbal_sent_ms = self.gimbal_cmd, now
            self._send(BridgeCommand(type="gimbal", ts_ms=now, payload={"pitch_deg": round(self.gimbal_cmd, 1)}))
        return self.state(), []

    def _yaw_to(self, d_xy, target_xy, heading) -> float:
        desired = heading_from_vector(target_xy[0] - d_xy[0], target_xy[1] - d_xy[1])
        return float(np.clip(1.4 * wrap_deg(desired - heading), -self.cfg.max_yaw_rate_dps, self.cfg.max_yaw_rate_dps))

    def _alt_hold(self, inp: AutonomyInputs, dt: float, target: float | None = None) -> float:
        if inp.drone.alt_m is None:
            return 0.0
        return self.pid_alt.step((target or self.cfg.altitude_m) - inp.drone.alt_m, dt)

    def _ibvs(self, inp: AutonomyInputs, dt: float) -> tuple[float, float, float, float]:
        W, H = inp.image_size
        x0, y0, x1, y1 = inp.subject_box  # type: ignore[misc]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ex_deg = (cx - W / 2) / (W / 2) * inp.hfov_deg / 2
        ey_deg = (cy - 0.52 * H) / (H / 2) * inp.vfov_deg / 2
        yaw = self.pid_yaw.step(ex_deg, dt)
        d = inp.drone
        fwd = right = 0.0
        if inp.calibration_valid and inp.subject_xy and d.xy and d.heading_deg is not None:
            dx, dy = inp.subject_xy[0] - d.xy[0], inp.subject_xy[1] - d.xy[1]
            horiz = math.hypot(dx, dy)
            h = math.radians(d.heading_deg)
            fv = np.array([math.sin(h), math.cos(h)])
            rv = np.array([math.cos(h), -math.sin(h)])
            vx, vy = inp.subject_vel or (0.0, 0.0)
            v = np.array([vx, vy])
            fwd = self.pid_fwd.step(horiz - self.cfg.standoff_m, dt) + 0.9 * float(np.dot(v, fv))
            right = 0.9 * float(np.dot(v, rv))
            if d.alt_m:
                self.gimbal_cmd = float(np.clip(-math.degrees(math.atan2(d.alt_m, max(horiz, 1.0))), -90.0, -25.0))
        else:
            box_h = y1 - y0
            fwd = float(np.clip(0.05 * (0.11 * H - box_h), -2.0, 2.0))
            if inp.gimbal_pitch_deg is not None:
                self.gimbal_cmd = float(np.clip(inp.gimbal_pitch_deg - 0.4 * ey_deg, -90.0, -25.0))
        return fwd, right, self._alt_hold(inp, dt), yaw

    def _primitive(self, inp: AutonomyInputs, dt: float) -> tuple[float, float, float, float]:
        d = inp.drone
        p = self.primitive or "hold_and_alert"
        if d.xy is None or d.heading_deg is None:
            return 0.0, 0.0, self._alt_hold(inp, dt), 0.0
        h = math.radians(d.heading_deg)
        fv = np.array([math.sin(h), math.cos(h)])
        rv = np.array([math.cos(h), -math.sin(h)])
        pos = np.array(d.xy)
        if p == "continue_predicted" and inp.subject_xy:
            tgt = np.array(inp.subject_xy)
            to = tgt - pos
            dist = float(np.linalg.norm(to))
            want = max(0.0, dist - self.cfg.standoff_m * 0.8)
            v = to / max(dist, 1e-6) * min(self.cfg.reacquire_max_speed_mps, 0.8 * want)
            if inp.subject_vel:
                v = v + 0.7 * np.array(inp.subject_vel)
            return float(np.dot(v, fv)), float(np.dot(v, rv)), self._alt_hold(inp, dt), self._yaw_to(d.xy, tgt, d.heading_deg)
        if p.startswith("route:") and self.route:
            while self.route_i < len(self.route) - 1 and np.linalg.norm(self.route[self.route_i] - pos) < 3.0:
                self.route_i += 1
            tgt = self.route[self.route_i]
            to = tgt - pos
            dist = float(np.linalg.norm(to))
            if self.route_i == len(self.route) - 1 and dist < 4.0 and self.route_look_at is not None:
                # at the viewpoint: hold there and keep the camera on the target the viewpoint was chosen for
                look = self.route_look_at
                v = to * 0.6
                self.gimbal_cmd = -55.0
                return float(np.dot(v, fv)), float(np.dot(v, rv)), self._alt_hold(inp, dt), \
                    self._yaw_to(d.xy, (float(look[0]), float(look[1])), d.heading_deg)
            if self.route_i == len(self.route) - 1 and dist < 4.0:
                p = "orbit_last_seen"  # at the end of the route: circle it and keep looking
                inp = AutonomyInputs(**{**inp.__dict__, "last_seen_xy": (float(tgt[0]), float(tgt[1]))})
            else:
                v = to / max(dist, 1e-6) * min(self.cfg.reacquire_max_speed_mps, 0.9 * dist + 1.0)
                self.gimbal_cmd = -55.0
                return float(np.dot(v, fv)), float(np.dot(v, rv)), self._alt_hold(inp, dt), \
                    self._yaw_to(d.xy, (float(tgt[0]), float(tgt[1])), d.heading_deg)
        if p in ("yaw_left_scan", "yaw_right_scan"):
            return 0.0, 0.0, self._alt_hold(inp, dt), -30.0 if p == "yaw_left_scan" else 30.0
        if p == "climb_and_widen":
            if self._climb_base is None:
                self._climb_base = d.alt_m or self.cfg.altitude_m
            self.gimbal_cmd = -40.0
            tgt_alt = min(self.cfg.max_altitude_m, (self._climb_base or self.cfg.altitude_m) + 5.0)
            yaw = self._yaw_to(d.xy, inp.subject_xy, d.heading_deg) if inp.subject_xy else 0.0
            return 0.0, 0.0, self._alt_hold(inp, dt, tgt_alt), yaw
        if p == "orbit_last_seen" and (inp.last_seen_xy or inp.subject_xy):
            c = np.array(inp.last_seen_xy or inp.subject_xy)  # type: ignore[arg-type]
            rel = pos - c
            r = float(np.linalg.norm(rel))
            if self._orbit_angle is None:
                self._orbit_angle = math.atan2(rel[1], rel[0])
            radius = 9.0
            radial = (r - radius) * -0.6
            tang = np.array([-rel[1], rel[0]]) / max(r, 1e-6)
            v = tang * 2.2 + (rel / max(r, 1e-6)) * radial
            self.gimbal_cmd = float(np.clip(-math.degrees(math.atan2(d.alt_m or 18.0, radius)), -90, -30))
            return float(np.dot(v, fv)), float(np.dot(v, rv)), self._alt_hold(inp, dt), self._yaw_to(d.xy, c, d.heading_deg)
        return 0.0, 0.0, self._alt_hold(inp, dt), 0.0

    # ---------------------------------------------------------------------------------------------------------
    def state(self) -> AutonomyState:
        note = self.note
        if not self.commands_enabled and self.bridge is not None and self.mode != "MANUAL":
            note = "ADVISORY ONLY: real-flight virtual stick disabled (autonomy.allow_real_flight=false)"
        link = "none" if self.bridge is None else ("sim" if self.target_is_sim else "real")
        return AutonomyState(mode=self.mode, fsm=self.fsm, motion_profile=self.cfg.motion_profile,
                             target_is_sim=self.target_is_sim, link=link,  # type: ignore[arg-type]
                             real_flight_allowed=self.cfg.allow_real_flight, commands_enabled=self.commands_enabled,
                             primitive=self.primitive, primitive_source=self.primitive_source,  # type: ignore[arg-type]
                             last_command=self.last_cmd if self.mode in ("FOLLOW", "HOLD", "DISPATCH", "GOTO") else None,
                             gimbal_cmd_deg=self.gimbal_cmd, safety=self.safety_status, note=note,
                             route=self._route_view(), route_label=self._route_label_view(),
                             reflex=self.reflex_reason, maneuver=self.maneuver[0] if self.maneuver else None,
                             scene_ahead_m=self.scene["path_ahead_m"] if self.scene else None)

    def _route_view(self) -> list[tuple[float, float]] | None:
        """The waypoints still to fly: a pursuit route, or the dispatch's way to the area and its search legs."""
        if self.mode == "DISPATCH" and self.plan is not None:
            left = self.plan.waypoints[self.plan.wp_i:]
            return [(round(float(w[0]), 1), round(float(w[1]), 1)) for w in left] or None
        return [(round(float(w[0]), 1), round(float(w[1]), 1)) for w in self.route] or None

    def _route_label_view(self) -> str | None:
        if self.mode == "DISPATCH" and self.plan is not None:
            n = len(self.plan.waypoints)
            return {"TAKEOFF": "taking off", "TRANSIT": "to the search area",
                    "SEARCH": f"search leg {max(1, self.plan.wp_i)} of {n}", "COVERED": "area covered: orbiting"}.get(self.plan.phase)
        return self.route_label


def _in_poly(poly, x, y) -> bool:
    from .safety import inside
    return inside(poly, x, y)
