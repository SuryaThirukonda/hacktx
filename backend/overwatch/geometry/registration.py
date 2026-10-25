"""Registration of the phone's ARKit session frame into the local map frame (T_local_from_arkit).

ARKit's origin/yaw are arbitrary and drift, and they are not GPS north. Methods (Rescue Copilot §10.2):
* ``auto_trajectory``: least-squares 2D rigid alignment (Kabsch) between the ARKit trajectory and the
  drone-observed responder track over a sliding window; gated by travel distance and residual;
* ``compass_point``: yaw from a good compass reading + one co-located point (low/medium confidence);
* ``manual_two_point``: operator-entered (low confidence).
Horizontal ARKit coordinates use (X, -Z): "forward at session start" is +b.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from ..config import RegistrationCfg
from ..schemas import RegistrationStatus
from .camera import wrap_deg


def kabsch_2d(A: np.ndarray, B: np.ndarray, w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    """R, t minimising sum w_i ||R a_i + t - b_i||^2 (rotation only, no scale). Returns (R, t, rms residual)."""
    A = np.asarray(A, float)
    B = np.asarray(B, float)
    w = np.ones(len(A)) if w is None else np.asarray(w, float)
    w = w / w.sum()
    ca = (w[:, None] * A).sum(axis=0)
    cb = (w[:, None] * B).sum(axis=0)
    Hm = ((A - ca) * w[:, None]).T @ (B - cb)
    U, _, Vt = np.linalg.svd(Hm)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, d])
    R = Vt.T @ D @ U.T
    t = cb - R @ ca
    res = (A @ R.T + t) - B
    rms = float(np.sqrt((w * (res ** 2).sum(axis=1)).sum()))
    return R, t, rms


def arkit_horizontal(pose_4x4: list[list[float]] | np.ndarray) -> tuple[np.ndarray, float]:
    """(X, -Z) position and heading (deg, clockwise from +b) of an ARKit camera pose."""
    T = np.asarray(pose_4x4, float)
    pos = np.array([T[0, 3], -T[2, 3]])
    fwd = -T[:3, 2]
    heading = math.degrees(math.atan2(fwd[0], -fwd[2])) % 360.0
    return pos, heading


@dataclass
class _Pair:
    t_ms: float
    ark: np.ndarray
    local: np.ndarray
    sigma: float


class PhoneRegistration:
    def __init__(self, cfg: RegistrationCfg) -> None:
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.pairs: deque[_Pair] = deque(maxlen=600)
        self.R: np.ndarray | None = None
        self.t: np.ndarray | None = None
        self.status = RegistrationStatus()
        self._last_solve_ms = -1e9

    def add_pair(self, t_ms: float, ark_xy: np.ndarray, local_xy: tuple[float, float], sigma_m: float) -> None:
        self.pairs.append(_Pair(t_ms, np.asarray(ark_xy, float), np.asarray(local_xy, float), max(0.05, sigma_m)))

    def solve(self, now_ms: float) -> RegistrationStatus:
        cfg = self.cfg
        if now_ms - self._last_solve_ms < 500:
            return self.status
        self._last_solve_ms = now_ms
        window = [p for p in self.pairs if now_ms - p.t_ms <= cfg.window_s * 1000.0]
        if len(window) < cfg.min_pairs:
            if self.status.method != "auto_trajectory":
                return self.status
            self.status.reason = f"auto registration ageing ({len(window)} recent pairs)"
            return self.status
        A = np.array([p.ark for p in window])
        B = np.array([p.local for p in window])
        travel = float(np.linalg.norm(A.max(axis=0) - A.min(axis=0)))
        if travel < cfg.min_travel_m:
            if self.status.method == "none":
                self.status = RegistrationStatus(reason=f"waiting for responder to move ({travel:.1f}/{cfg.min_travel_m} m)",
                                                 pairs=len(window))
            return self.status
        w = np.array([1.0 / p.sigma ** 2 for p in window])
        R, t, rms = kabsch_2d(A, B, w)
        # one robust pass: drop the worst 20% then refit
        res = np.linalg.norm(A @ R.T + t - B, axis=1)
        keep = res <= np.percentile(res, 80)
        if keep.sum() >= cfg.min_pairs:
            R, t, rms = kabsch_2d(A[keep], B[keep], w[keep])
        yaw = math.degrees(math.atan2(R[1, 0], R[0, 0]))
        valid = rms <= cfg.max_residual_m
        conf = "high" if rms <= cfg.max_residual_m * 0.5 else ("medium" if valid else "none")
        if valid:
            self.R, self.t = R, t
        self.status = RegistrationStatus(
            method="auto_trajectory", valid=valid or self.R is not None, confidence=conf if valid else self.status.confidence,
            residual_m=round(rms, 3), pairs=int(keep.sum()), yaw_deg=round(yaw, 2),
            translation_m=(round(float(t[0]), 3), round(float(t[1]), 3)),
            reason="ARKit trajectory aligned to drone-observed responder track" if valid
            else f"residual {rms:.2f} m > {cfg.max_residual_m} m (keeping previous transform)" if self.R is not None
            else f"residual {rms:.2f} m > {cfg.max_residual_m} m")
        return self.status

    def set_compass_point(self, ark_xy: np.ndarray, ark_heading_deg: float, local_heading_deg: float,
                          local_xy: tuple[float, float]) -> RegistrationStatus:
        # rotation maps ark heading -> local heading: local = ark - yaw(ccw) in clockwise-heading terms
        yaw_ccw = math.radians(-wrap_deg(local_heading_deg - ark_heading_deg))
        R = np.array([[math.cos(yaw_ccw), -math.sin(yaw_ccw)], [math.sin(yaw_ccw), math.cos(yaw_ccw)]])
        t = np.asarray(local_xy, float) - R @ np.asarray(ark_xy, float)
        self.R, self.t = R, t
        self.status = RegistrationStatus(method="compass_point", valid=True, confidence="low", residual_m=None,
                                         pairs=1, yaw_deg=round(math.degrees(yaw_ccw), 2),
                                         translation_m=(round(float(t[0]), 3), round(float(t[1]), 3)),
                                         reason="compass heading + one co-located point (approximate)")
        return self.status

    def set_manual_two_point(self, ark_pts: list[tuple[float, float]], local_pts: list[tuple[float, float]]) -> RegistrationStatus:
        if len(ark_pts) < 2 or len(local_pts) < 2:
            raise ValueError("need two ARKit and two local points")
        R, t, rms = kabsch_2d(np.array(ark_pts[:2]), np.array(local_pts[:2]))
        self.R, self.t = R, t
        self.status = RegistrationStatus(method="manual_two_point", valid=True, confidence="low", residual_m=round(rms, 3),
                                         pairs=2, yaw_deg=round(math.degrees(math.atan2(R[1, 0], R[0, 0])), 2),
                                         translation_m=(round(float(t[0]), 3), round(float(t[1]), 3)),
                                         reason="manual two-point alignment (low confidence)")
        return self.status

    # ---------------------------------------------------------------------------------------------------------
    @property
    def valid(self) -> bool:
        return self.R is not None and self.status.valid

    def to_local(self, ark_xy: np.ndarray) -> np.ndarray | None:
        if self.R is None or self.t is None:
            return None
        P = np.asarray(ark_xy, float).reshape(-1, 2)
        return P @ self.R.T + self.t

    def heading_to_local(self, ark_heading_deg: float) -> float | None:
        if self.R is None:
            return None
        yaw_ccw = math.degrees(math.atan2(self.R[1, 0], self.R[0, 0]))
        return (ark_heading_deg - yaw_ccw) % 360.0

    @property
    def error_m(self) -> float:
        if self.status.residual_m is not None:
            return max(0.2, self.status.residual_m * 1.5)
        return 1.5
