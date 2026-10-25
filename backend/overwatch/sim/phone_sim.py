"""Synthetic responder iPhone: ARKit-like pose (own session frame + drift), LiDAR depth (z-buffer rasterised
against the same 3D world), compass/GPS, and an RGB camera view. Depth is summarised with the same reference
algorithm the native app runs, so the backend receives an identical `PhoneObservation`."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import Config
from ..geometry.camera import CameraPose, Intrinsics, PinholeCamera, heading_from_vector, rotation_world_to_cam
from ..geometry.geo import local_to_latlon
from ..geometry.phone_depth import ARKIT_FROM_CV, DepthFrame, PhoneDepthProcessor
from ..geometry.phone_view import median_range, orientation_from_pose, rotate_to_upright, upright_point
from ..schemas import GPSFix, PhoneObservation
from .actors import Actor
from .render import NEAR, RenderEffects, SceneRenderer, _ring
from .world import World
from ..vision.detectors import SimBlobDetector


@dataclass
class PhoneFrame:
    observation: PhoneObservation
    rgb: np.ndarray | None  # upright, as uplinked
    depth: DepthFrame | None  # sensor-native
    true_pose: CameraPose
    box_gt: list[dict] | None = None  # per vision box: {"actor": id | None, "range_m": true LiDAR range | None}


class PhoneSim:
    def __init__(self, cfg: Config, world: World, renderer: SceneRenderer, holder_id: str = "responder",
                 seed: int = 3) -> None:
        self.cfg = cfg
        self.world = world
        self.renderer = renderer
        self.holder_id = holder_id
        pc = cfg.sources.phone
        self.rgb_intr = Intrinsics.from_hfov(pc.video_width, pc.video_height, pc.hfov_deg)
        self.depth_intr = self.rgb_intr.scaled(256, 192)
        self.processor = PhoneDepthProcessor(cfg.phone)
        self.rng = np.random.default_rng(seed)
        self.origin: np.ndarray | None = None  # ARKit session origin (local ENU) and yaw
        self.origin_heading = 0.0
        self.seq = 0
        self.yaw_drift = 0.0
        self.trans_drift = np.zeros(2)
        self.gps_err = np.zeros(2)
        self.compass_bias = float(self.rng.normal(0, 2.5))
        self._last_gps_ms = -1e9
        self._gps: GPSFix | None = None
        self._last_t = None
        self.vision = SimBlobDetector(min_area=30)
        self.orientation = "up"

    def reset(self) -> None:
        self.origin = None
        self.processor.reset()
        self.yaw_drift = 0.0
        self.trans_drift[:] = 0

    # ---------------------------------------------------------------------------------------------------------
    def true_pose(self, holder: Actor, t_s: float, portrait: bool = False) -> CameraPose:
        h = math.radians(holder.heading)
        fwd = np.array([math.sin(h), math.cos(h)])
        speed = float(np.linalg.norm(holder.vel))
        bob = 0.025 * math.sin(holder.phase * 2) * min(1.0, speed)
        pos = np.array([holder.pos[0] + fwd[0] * 0.32, holder.pos[1] + fwd[1] * 0.32, 1.32 + bob])
        sway = 2.0 * math.sin(t_s * 0.9) + 1.2 * math.sin(t_s * 2.3 + 1.0)
        # held upright, the landscape sensor is rolled 90 deg about the optical axis (image right points down)
        roll = 0.8 * math.sin(t_s * 1.3) + (90.0 if portrait else 0.0)
        return CameraPose(pos, (holder.heading + sway) % 360.0, -9.0 + 1.5 * math.sin(t_s * 1.7), roll)

    def _ark_from_local(self) -> tuple[np.ndarray, np.ndarray]:
        h0 = math.radians(self.origin_heading + self.yaw_drift)
        fwd0 = np.array([math.sin(h0), math.cos(h0), 0.0])
        right0 = np.array([math.cos(h0), -math.sin(h0), 0.0])
        up = np.array([0.0, 0.0, 1.0])
        M = np.stack([right0, up, -fwd0])
        origin = self.origin.copy()
        origin[:2] += self.trans_drift
        return M, origin

    def arkit_pose(self, pose: CameraPose) -> np.ndarray:
        M, origin = self._ark_from_local()
        R_cw = rotation_world_to_cam(pose.heading_deg, pose.pitch_deg, pose.roll_deg)
        R_wc = R_cw.T
        T = np.eye(4)
        T[:3, :3] = M @ R_wc @ ARKIT_FROM_CV
        T[:3, 3] = M @ (pose.position - origin)
        return T

    # ---------------------------------------------------------------------------------------------------------
    def step(self, t_ms: float, actors: list[Actor], faults: dict, want_rgb: bool = True) -> PhoneFrame | None:
        holder = next((a for a in actors if a.id == self.holder_id and a.active), None)
        if holder is None:
            return None
        t_s = t_ms / 1000.0
        dt = 0.0 if self._last_t is None else max(0.0, t_s - self._last_t)
        self._last_t = t_s
        pose = self.true_pose(holder, t_s, bool(faults.get("phone_portrait")))
        if self.origin is None:
            self.origin = pose.position.copy()
            self.origin[2] = pose.position[2]
            self.origin_heading = pose.heading_deg
        # ARKit drift: slow yaw drift + translation drift proportional to motion
        speed = float(np.linalg.norm(holder.vel))
        self.yaw_drift += dt * (0.4 / 60.0) * (1.0 + speed)
        self.trans_drift += self.rng.normal(0, 0.004, 2) * speed * dt * 10

        others = [a for a in actors if a.id != self.holder_id]
        cam = PinholeCamera(self.rgb_intr, pose)
        ark_pose = self.arkit_pose(pose)
        self.orientation = orientation_from_pose(ark_pose, self.orientation)
        rgb = None
        if want_rgb and not faults.get("phone_video_drop"):
            fx = RenderEffects(noise_sigma=2.5, fog_m=85.0, vignette=0.18,
                               brightness=0.45 if faults.get("low_light") else 1.0,
                               blur_px=6.0 if speed > 2.5 else 0.0)
            sensor_rgb = self.renderer.render(cam, others, fx, ground_level=True, with_gt=False).image
            rgb = np.ascontiguousarray(rotate_to_upright(sensor_rgb, self.orientation))

        tracking = "normal"
        if faults.get("arkit_limited"):
            tracking = "limited"
        depth_frame = None
        result = None
        if not faults.get("lidar_unsupported"):
            depth, conf = self.render_depth(pose, others)
            if faults.get("lidar_dropout"):
                conf[:] = 0
            depth_frame = DepthFrame(depth, conf, self.depth_intr.K, ark_pose, t_ms)
            result = self.processor.process(depth_frame)

        # on-device person boxes (Apple Vision in the native app, on the upright image) + LiDAR range inside each
        # box, sampled in the sensor-native depth map through the same orientation mapping as the app
        vboxes, vranges, box_gt = None, None, None
        if rgb is not None:
            H, W = rgb.shape[:2]
            dets = [d for d in self.vision.detect(rgb) if (d.box[3] - d.box[1]) > 18]
            vboxes, vranges, box_gt = [], [], []
            for d in dets[:8]:
                x0, y0, x1, y1 = d.box
                bn = (round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4))
                vboxes.append(bn)
                vranges.append(median_range(bn, self.orientation, depth_frame.depth, depth_frame.confidence)
                               if depth_frame is not None else None)
                box_gt.append(self._box_truth(cam, others, bn))

        # compass + GPS (1 Hz)
        heading = (pose.heading_deg + self.compass_bias + float(self.rng.normal(0, 3.0))) % 360.0
        if t_ms - self._last_gps_ms >= 1000.0:
            self._last_gps_ms = t_ms
            self.gps_err = 0.85 * self.gps_err + self.rng.normal(0, 1.2, 2)
            geo = self.cfg.world.geo_reference
            if geo is not None:
                lat, lon = local_to_latlon(holder.pos[0] + self.gps_err[0], holder.pos[1] + self.gps_err[1], geo)
                self._gps = GPSFix(lat=lat, lon=lon, accuracy_m=5.0)
        self.seq += 1
        obs = PhoneObservation(
            source_id="sim-responder-iphone",
            seq=self.seq,
            capture_ts_ms=t_ms,
            arkit_tracking_state=tracking,
            tracking_reason="excessiveMotion" if tracking == "limited" else None,
            camera_pose_4x4=ark_pose.round(5).tolist() if tracking == "normal" else None,
            camera_intrinsics_3x3=self.depth_intr.K.round(4).tolist(),
            image_resolution=(self.rgb_intr.width, self.rgb_intr.height),
            image_orientation=self.orientation,  # type: ignore[arg-type]
            lidar_supported=not faults.get("lidar_unsupported", False),
            depth_mode="sceneDepth" if result is not None else "none",
            depth_valid_fraction=round(result.valid_fraction, 3) if result else None,
            sectors=result.sectors if result else [],
            obstacle_points=result.obstacle_points if (result and tracking == "normal") else None,
            floor_y_m=result.floor_y_m if result else None,
            heading_deg=round(heading, 2),
            heading_accuracy_deg=10.0,
            gps=self._gps,
            battery_pct=88.0,
            local_alert_active=bool(result.proximity_alert) if result else False,
            vision_boxes=vboxes,
            vision_ranges_m=vranges,
        )
        return PhoneFrame(obs, rgb, depth_frame, pose, box_gt)

    def _box_truth(self, cam: PinholeCamera, others: list[Actor], box_n: tuple[float, float, float, float]) -> dict:
        """Ground truth for one upright vision box: the nearest actor whose torso projects inside it, and the
        LiDAR range a perfect mapping would read there (the depth raster puts the body surface 0.12 m in front)."""
        x0, y0, x1, y1 = box_n
        best = None
        for a in others:
            if not a.active:
                continue
            uv, z = cam.project(np.array([[a.pos[0], a.pos[1], 1.0]]))
            if z[0] <= NEAR:
                continue
            ux, uy = upright_point((uv[0, 0] / cam.intr.width, uv[0, 1] / cam.intr.height), self.orientation)
            if x0 <= ux <= x1 and y0 <= uy <= y1 and (best is None or z[0] < best[1]):
                best = (a.id, float(z[0]))
        if best is None:
            return {"actor": None, "range_m": None, "bearing_deg": None}
        a = next(o for o in others if o.id == best[0])
        dx, dy = a.pos[0] - cam.pose.position[0], a.pos[1] - cam.pose.position[1]
        bearing = (heading_from_vector(dx, dy) - cam.pose.heading_deg + 540.0) % 360.0 - 180.0
        return {"actor": best[0], "range_m": round(best[1] - 0.12, 3), "bearing_deg": round(bearing, 2)}

    # ---------------------------------------------------------------------------------------------------------
    def render_depth(self, pose: CameraPose, actors: list[Actor]) -> tuple[np.ndarray, np.ndarray]:
        """Z-buffer depth (meters along the optical axis) + ARKit-style confidence at 256x192."""
        intr = self.depth_intr
        cam = PinholeCamera(intr, pose)
        W, H = intr.width, intr.height
        uu, vv = np.meshgrid(np.arange(W, dtype=np.float32) + 0.5, np.arange(H, dtype=np.float32) + 0.5)
        xr = (uu - intr.cx) / intr.fx
        yr = (vv - intr.cy) / intr.fy
        a, b, c = cam.ray_z_coefficients()
        rz = a * uu + b * vv + c
        cz = float(pose.position[2])
        with np.errstate(divide="ignore", invalid="ignore"):
            zbuf = np.where(rz < -1e-6, -cz / rz, np.inf).astype(np.float32)
        reflective = np.zeros((H, W), bool)

        def plane_fill(poly_world: np.ndarray, refl: bool = False) -> None:
            pc = cam.world_to_cam(poly_world)
            if (pc[:, 2] < NEAR).all():
                return
            from .render import _clip_near
            if (pc[:, 2] < NEAR).any():
                pc = _clip_near(pc)
                if len(pc) < 3:
                    return
            uv = np.stack([intr.fx * pc[:, 0] / pc[:, 2] + intr.cx, intr.fy * pc[:, 1] / pc[:, 2] + intr.cy], axis=1)
            n = np.cross(pc[1] - pc[0], pc[2] - pc[0])
            nn = np.linalg.norm(n)
            if nn < 1e-9:
                return
            n /= nn
            mask = np.zeros((H, W), np.uint8)
            cv2.fillPoly(mask, [np.round(uv * 4).astype(np.int32)], 1, cv2.LINE_8, shift=2)
            m = mask.astype(bool)
            if not m.any():
                return
            denom = n[0] * xr[m] + n[1] * yr[m] + n[2]
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(np.abs(denom) > 1e-6, float(np.dot(n, pc[0])) / denom, np.inf)
            z = np.where(z > NEAR, z, np.inf)
            cur = zbuf[m]
            better = z < cur
            zbuf[m] = np.where(better, z, cur)
            if refl:
                rm = reflective[m]
                reflective[m] = np.where(better, True, rm)

        def billboard(points_world: np.ndarray, depth: float) -> None:
            uv, z = cam.project(points_world)
            keep = z > NEAR
            if keep.sum() < 3 or depth <= NEAR:
                return
            hull = cv2.convexHull(uv[keep].astype(np.float32)).reshape(-1, 2)
            mask = np.zeros((H, W), np.uint8)
            cv2.fillPoly(mask, [np.round(hull * 4).astype(np.int32)], 1, cv2.LINE_8, shift=2)
            m = mask.astype(bool)
            zbuf[m] = np.minimum(zbuf[m], depth)

        boxes, trees = self.world.boxes, self.world.trees
        if len(boxes) + len(trees) > 60:  # large worlds: LiDAR confidence is zero past 4.6 m, so only nearby objects
            px, py = float(pose.position[0]), float(pose.position[1])
            boxes = [b for b in boxes if math.hypot(b.center_xy[0] - px, b.center_xy[1] - py) < 60.0]
            trees = [t for t in trees if math.hypot(t.xy[0] - px, t.xy[1] - py) < 60.0]
        for bx in boxes:
            cs = bx.corners()
            for face, n in bx.faces():
                if np.dot(n, pose.position - cs[face].mean(axis=0)) > 0:
                    plane_fill(cs[face], refl=bx.reflective and n[2] < 0.5)
        ex, ey, up = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])
        for a in actors:
            if not a.active:
                continue
            p = np.array([a.pos[0], a.pos[1], 0.0])
            body = np.vstack([_ring(p, ex, ey, 0.25, 0.25, 8), _ring(p + up * a.height, ex, ey, 0.25, 0.25, 8)])
            dc = cam.world_to_cam((p + up * 1.0)[None, :])[0, 2] - 0.12
            billboard(body, float(dc))
        for tr in trees:
            p = np.array([tr.xy[0], tr.xy[1], 0.0])
            trunk = np.vstack([_ring(p, ex, ey, tr.trunk_r, tr.trunk_r, 8), _ring(p + up * tr.trunk_h, ex, ey, tr.trunk_r, tr.trunk_r, 8)])
            billboard(trunk, float(cam.world_to_cam((p + up)[None, :])[0, 2]) - tr.trunk_r)
            canopy = _ring(p + up * tr.canopy_z, ex, ey, tr.canopy_r, tr.canopy_r, 12)
            canopy = np.vstack([canopy, canopy + up * 0.01])
            billboard(np.vstack([canopy - up * tr.canopy_r * 0.7, canopy + up * tr.canopy_r * 0.7]),
                      float(cam.world_to_cam((p + up * tr.canopy_z)[None, :])[0, 2]) - tr.canopy_r * 0.6)
        for cn in self.world.cones:
            p = np.array([cn.xy[0], cn.xy[1], 0.0])
            cone = np.vstack([_ring(p, ex, ey, cn.r, cn.r, 8), (p + up * cn.h)[None, :]])
            billboard(cone, float(cam.world_to_cam((p + up * 0.2)[None, :])[0, 2]) - cn.r * 0.5)

        depth = np.where(np.isfinite(zbuf), zbuf, 0.0).astype(np.float32)
        noise = self.rng.normal(0.0, 1.0, depth.shape).astype(np.float32) * (0.004 + 0.004 * depth)
        depth = np.where(depth > 0, depth * (1.0 + noise), 0.0)
        conf = np.full(depth.shape, 2, np.uint8)
        conf[depth > 3.5] = 1
        conf[(depth > 4.6) | (depth <= 0)] = 0
        conf[reflective] = 0
        grazing = (rz > -0.12) & (depth > 2.5)
        conf[grazing & (conf > 1)] = 1
        speckle = self.rng.random(depth.shape) < 0.01
        conf[speckle] = 0
        depth[depth > 5.5] = 0.0
        return depth.astype(np.float32), conf
