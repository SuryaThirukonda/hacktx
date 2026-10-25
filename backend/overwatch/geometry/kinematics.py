"""Responder-relative kinematics (Overwatch §5.4 steps 3-7), computed only from validated geometry.

closing speed is reported two ways:
* ``closing_mps``: the *subject-induced* component (subject velocity toward the responder) — used by the alert
  rules so a responder walking up to a stationary subject does not trigger a "closing" alert (scenario OW5);
* the total range-rate (-dR/dt over a 0.8 s regression window) is kept in the history for display/eval.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..schemas import RelativeKinematics
from .camera import heading_from_vector, wrap_deg
from .kalman import CAFilter


def clock_position(bearing_rel_deg: float) -> int:
    c = int(round((bearing_rel_deg % 360.0) / 30.0)) % 12
    return 12 if c == 0 else c


class KinematicsTracker:
    def __init__(self, window_s: float = 0.8, min_samples: int = 5) -> None:
        self.window_ms = window_s * 1000.0
        self.min_samples = min_samples
        self.hist: deque[tuple[float, float]] = deque(maxlen=200)
        self.range_rate: float | None = None

    def reset(self) -> None:
        self.hist.clear()
        self.range_rate = None

    def compute(self, now_ms: float, subject: CAFilter | None, subject_err_m: float | None,
                responder_xy: tuple[float, float] | None, responder_err_m: float | None,
                responder_heading_deg: float | None, subject_fresh: bool, common_frame: bool,
                calib_valid: bool) -> RelativeKinematics:
        if subject is None:
            return RelativeKinematics(valid=False, reason="no subject lock")
        if not calib_valid:
            return RelativeKinematics(valid=False, reason="calibration invalid: position unknown")
        if responder_xy is None:
            return RelativeKinematics(valid=False, reason="responder position unknown (not locked / not registered)")
        if not subject_fresh:
            return RelativeKinematics(valid=False, reason="subject track stale: no current position claim")
        sx, sy = subject.position
        dx, dy = sx - responder_xy[0], sy - responder_xy[1]
        rng = math.hypot(dx, dy)
        # errors: drone-frame pairs share calibration bias, so only the per-point parts add
        se, re = subject_err_m or 0.5, responder_err_m or 1.0
        rng_err = math.hypot(se, re) * (0.6 if common_frame else 1.0)
        self.hist.append((now_ms, rng))
        recent = [(t, r) for t, r in self.hist if now_ms - t <= self.window_ms]
        if len(recent) >= self.min_samples:
            ts = np.array([t for t, _ in recent]) / 1000.0
            rs = np.array([r for _, r in recent])
            A = np.vstack([ts - ts.mean(), np.ones(len(ts))]).T
            slope = float(np.linalg.lstsq(A, rs, rcond=None)[0][0])
            self.range_rate = slope
        vx, vy = subject.velocity
        sp = math.hypot(vx, vy)
        to_resp = (-dx / rng, -dy / rng) if rng > 1e-6 else (0.0, 0.0)
        closing_subject = vx * to_resp[0] + vy * to_resp[1]
        align = closing_subject / sp if sp > 0.3 else None
        ttc = rng / closing_subject if closing_subject > 0.3 else None
        bearing_abs = heading_from_vector(dx, dy)
        bearing_rel = wrap_deg(bearing_abs - responder_heading_deg) if responder_heading_deg is not None else None
        ax, ay = subject.accel
        if rng_err > max(3.0, 0.6 * rng):
            return RelativeKinematics(valid=False, reason=f"range uncertainty too large (±{rng_err:.1f} m)",
                                      range_m=round(rng, 2), range_error_m=round(rng_err, 2))
        return RelativeKinematics(
            valid=True, reason="ok" if responder_heading_deg is not None else "ok (responder heading unknown)",
            range_m=round(rng, 2), range_error_m=round(rng_err, 2),
            bearing_rel_deg=round(bearing_rel, 1) if bearing_rel is not None else None,
            bearing_abs_deg=round(bearing_abs, 1),
            clock=clock_position(bearing_rel) if bearing_rel is not None else None,
            closing_mps=round(closing_subject, 2), ttc_s=round(ttc, 2) if ttc is not None else None,
            alignment_cos=round(align, 3) if align is not None else None,
            subject_speed_mps=round(sp, 2),
            subject_heading_deg=round(heading_from_vector(vx, vy), 1) if sp > 0.3 else None,
            subject_accel_mps2=round(math.hypot(ax, ay), 2),
            predicted_path_xy_m=[(round(x, 2), round(y, 2)) for x, y in subject.predicted_path(2.0, 0.25)],
        )
