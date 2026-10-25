"""Reference LiDAR depth -> obstacle-sector algorithm.

This runs on-device in the iOS app (ios/OverwatchResponder/DepthProcessor.swift mirrors it) and in the simulator,
so the Mac always receives the same `PhoneObservation` summary. Steps (Rescue Copilot §7.3):
1. reject invalid / low-confidence / out-of-range depth and report the valid fraction;
2. back-project to ARKit world (y up) with the camera pose;
3. estimate the floor height (dominant low horizontal mode) and exclude it;
4. obstacle points = height band above the floor; azimuth sectors relative to the phone's horizontal forward;
5. robust range per sector (10th percentile), support counts -> confidence;
6. a 3D walking corridor for the local proximity rule with persistence + hysteresis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..config import PhoneCfg
from ..schemas import DepthSector

ARKIT_FROM_CV = np.diag([1.0, -1.0, -1.0])

SECTORS: list[tuple[str, float, float]] = [
    ("left", -75.0, -40.0),
    ("front_left", -40.0, -12.0),
    ("front", -12.0, 12.0),
    ("front_right", 12.0, 40.0),
    ("right", 40.0, 75.0),
]


@dataclass
class DepthFrame:
    depth: np.ndarray  # (H, W) float32 meters along the optical axis; 0 / nan = invalid
    confidence: np.ndarray  # (H, W) uint8: 0 low, 1 medium, 2 high (ARConfidenceLevel)
    K: np.ndarray  # 3x3 intrinsics at depth resolution (pixel coords, OpenCV convention)
    pose_ark: np.ndarray  # 4x4 ARKit world-from-camera (ARKit camera axes: x right, y up, z back)
    ts_ms: float


@dataclass
class DepthResult:
    sectors: list[DepthSector]
    obstacle_points: list[tuple[float, float, float]]
    floor_y_m: float | None
    floor_source: str
    valid_fraction: float
    corridor_nearest_m: float | None
    proximity_alert: bool
    hfov_deg: float
    stats: dict = field(default_factory=dict)


class PhoneDepthProcessor:
    def __init__(self, cfg: PhoneCfg, step: int = 2) -> None:
        self.cfg = cfg
        self.step = step
        self.floor_y: float | None = None
        self._near_count = 0
        self._clear_count = 0
        self.alert_active = False

    def reset(self) -> None:
        self.floor_y = None
        self._near_count = self._clear_count = 0
        self.alert_active = False

    def process(self, frame: DepthFrame) -> DepthResult:
        cfg = self.cfg
        d = frame.depth[:: self.step, :: self.step].astype(np.float32)
        conf = frame.confidence[:: self.step, :: self.step]
        Hh, Ww = d.shape
        K = frame.K.copy()
        K[0, :] /= self.step
        K[1, :] /= self.step
        hfov = math.degrees(2 * math.atan((frame.depth.shape[1] / 2.0) / frame.K[0, 0]))

        valid = np.isfinite(d) & (d > 0.1) & (d <= cfg.max_range_m) & (conf >= 1)
        valid_fraction = float(valid.mean()) if valid.size else 0.0
        empty = DepthResult(self._unknown_sectors(), [], self.floor_y, "previous" if self.floor_y else "none",
                            valid_fraction, None, False, hfov)
        if valid.sum() < 10:
            self._update_alert(None)
            empty.proximity_alert = self.alert_active
            return empty

        vv, uu = np.nonzero(valid)
        z = d[vv, uu]
        x = (uu + 0.5 - K[0, 2]) * z / K[0, 0]
        y = (vv + 0.5 - K[1, 2]) * z / K[1, 1]
        p_cv = np.stack([x, y, z], axis=1)
        pose = frame.pose_ark
        R, t = pose[:3, :3], pose[:3, 3]
        P = (p_cv @ ARKIT_FROM_CV.T) @ R.T + t  # ARKit world, y up
        cam_y = float(t[1])
        hconf = conf[vv, uu]

        # --- floor: dominant mode of heights well below the camera -------------------------------------
        floor_src = "previous" if self.floor_y is not None else "assumed"
        low = P[:, 1] < cam_y - 0.6
        if low.sum() > max(30, 0.05 * len(P)):
            ys = P[low, 1]
            hist, edges = np.histogram(ys, bins=np.arange(ys.min() - 0.03, ys.max() + 0.06, 0.03))
            if hist.size:
                k = int(np.argmax(hist))
                if hist[k] > 0.25 * low.sum():
                    est = float((edges[k] + edges[k + 1]) / 2)
                    self.floor_y = est if self.floor_y is None else 0.7 * self.floor_y + 0.3 * est
                    floor_src = "measured"
        floor_y = self.floor_y if self.floor_y is not None else cam_y - 1.35

        h = P[:, 1] - floor_y
        obst = (h > cfg.min_obstacle_height_m) & (h < cfg.max_obstacle_height_m)

        # --- horizontal frame of the phone ---------------------------------------------------------------
        fwd3 = -R[:, 2]
        f = np.array([fwd3[0], fwd3[2]])
        fn = np.linalg.norm(f)
        f = f / fn if fn > 1e-6 else np.array([0.0, -1.0])
        r = np.array([-f[1], f[0]])
        rel = P[:, [0, 2]] - np.array([t[0], t[2]])
        fwd_d = rel @ f
        lat_d = rel @ r
        az = np.degrees(np.arctan2(lat_d, fwd_d))
        rng = np.hypot(fwd_d, lat_d)

        sectors: list[DepthSector] = []
        half_fov = hfov / 2.0
        for name, a0, a1 in SECTORS:
            if a1 <= -half_fov or a0 >= half_fov:
                sectors.append(DepthSector(sector=name))  # outside the sensor's field of view: unknown
                continue
            m = obst & (az >= a0) & (az < a1) & (fwd_d > 0.1)
            n = int(m.sum())
            if n < 5:
                # Visible but empty sector: report no obstacle only when the floor there was actually observed
                seen = (az >= a0) & (az < a1)
                nm = None
                c = "high" if seen.sum() > cfg.min_points * 3 else "unknown"
                sectors.append(DepthSector(sector=name, nearest_m=nm, confidence=c, num_valid_points=int(seen.sum())))
                continue
            nearest = float(np.percentile(rng[m], 10))
            hi = float((hconf[m] >= 2).mean())
            if n >= cfg.min_points * 3 and hi > 0.6:
                c = "high"
            elif n >= cfg.min_points:
                c = "medium"
            else:
                c = "low"
            sectors.append(DepthSector(sector=name, nearest_m=round(nearest, 3), confidence=c, num_valid_points=n))

        corridor = obst & (np.abs(lat_d) < cfg.corridor_half_width_m) & (fwd_d > 0.2) & (fwd_d < cfg.max_range_m)
        corridor_nearest = float(np.percentile(fwd_d[corridor], 10)) if corridor.sum() >= cfg.min_points else None
        self._update_alert(corridor_nearest)

        # --- voxel-downsampled obstacle cloud for the Mac's occupancy grid -----------------------------------
        pts: list[tuple[float, float, float]] = []
        if obst.any():
            O = P[obst]
            keys = np.floor(O / 0.1).astype(np.int64)
            _, idx = np.unique(keys, axis=0, return_index=True)
            O = O[np.sort(idx)][:400]
            pts = [(round(float(a), 3), round(float(b), 3), round(float(c), 3)) for a, b, c in O]

        return DepthResult(sectors, pts, self.floor_y, floor_src, valid_fraction, corridor_nearest,
                           self.alert_active, hfov,
                           stats={"points": int(len(P)), "obstacle_points": int(obst.sum()),
                                  "floor_points": int((np.abs(h) < 0.08).sum())})

    def _update_alert(self, nearest: float | None) -> None:
        thr = self.cfg.proximity_alert_m
        if nearest is not None and nearest < thr:
            self._near_count += 1
            self._clear_count = 0
        else:
            self._clear_count += 1
            self._near_count = 0
        if not self.alert_active and self._near_count >= self.cfg.persistence_frames:
            self.alert_active = True
        elif self.alert_active and self._clear_count >= 2 and (nearest is None or nearest > thr + 0.3):
            self.alert_active = False

    @staticmethod
    def _unknown_sectors() -> list[DepthSector]:
        return [DepthSector(sector=n) for n, _, _ in SECTORS]
