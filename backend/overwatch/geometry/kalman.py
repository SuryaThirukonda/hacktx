"""Ground-plane Kalman filters (local frame, meters, ms timestamps).

* ``CVFilter``: constant velocity, for every projected drone track.
* ``CAFilter``: constant acceleration with valid/stale flags (pattern from PX4 TargetEstimator), for the locked
  subject and responder; measurement noise comes from the projection error bound.
"""

from __future__ import annotations

import math

import numpy as np

from .camera import heading_from_vector


class CVFilter:
    def __init__(self, xy: tuple[float, float], t_ms: float, sigma_m: float, accel_sigma: float = 2.0) -> None:
        self.x = np.array([xy[0], xy[1], 0.0, 0.0])
        self.P = np.diag([sigma_m ** 2, sigma_m ** 2, 4.0, 4.0])
        self.t_ms = t_ms
        self.q = accel_sigma
        self.updates = 1
        self.first_ms = t_ms
        self.last_update_ms = t_ms

    def predict(self, t_ms: float) -> None:
        dt = max(0.0, (t_ms - self.t_ms) / 1000.0)
        if dt == 0:
            return
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = self.q ** 2
        G = np.array([[dt ** 2 / 2, 0], [0, dt ** 2 / 2], [dt, 0], [0, dt]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + G @ G.T * q
        self.t_ms = t_ms

    def update(self, xy: tuple[float, float], t_ms: float, sigma_m: float) -> None:
        self.predict(t_ms)
        Hm = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
        R = np.eye(2) * sigma_m ** 2
        y = np.array(xy) - Hm @ self.x
        S = Hm @ self.P @ Hm.T + R
        # gate wild jumps (calibration source switch): inflate R instead of trusting the outlier
        d2 = float(y @ np.linalg.inv(S) @ y)
        if d2 > 40.0:
            R = R * (d2 / 10.0)
            S = Hm @ self.P @ Hm.T + R
        K = self.P @ Hm.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ Hm) @ self.P
        self.updates += 1
        self.last_update_ms = t_ms

    @property
    def position(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[2]), float(self.x[3])

    @property
    def speed(self) -> float:
        return float(math.hypot(self.x[2], self.x[3]))

    @property
    def pos_sigma(self) -> float:
        return float(math.sqrt(max(self.P[0, 0], self.P[1, 1])))

    def heading(self) -> float | None:
        return heading_from_vector(self.x[2], self.x[3]) if self.speed > 0.3 else None


class CAFilter:
    """State [x, y, vx, vy, ax, ay]."""

    def __init__(self, xy: tuple[float, float], t_ms: float, sigma_m: float, jerk_sigma: float = 4.0) -> None:
        self.x = np.array([xy[0], xy[1], 0, 0, 0, 0], float)
        self.P = np.diag([sigma_m ** 2, sigma_m ** 2, 4.0, 4.0, 4.0, 4.0])
        self.t_ms = t_ms
        self.q = jerk_sigma
        self.last_update_ms = t_ms
        self.updates = 1

    def _F(self, dt: float) -> np.ndarray:
        F = np.eye(6)
        F[0, 2] = F[1, 3] = dt
        F[2, 4] = F[3, 5] = dt
        F[0, 4] = F[1, 5] = dt * dt / 2
        return F

    def predict(self, t_ms: float) -> None:
        dt = max(0.0, (t_ms - self.t_ms) / 1000.0)
        if dt == 0:
            return
        F = self._F(dt)
        G = np.array([[dt ** 3 / 6, 0], [0, dt ** 3 / 6], [dt ** 2 / 2, 0], [0, dt ** 2 / 2], [dt, 0], [0, dt]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + G @ G.T * self.q ** 2
        # damp acceleration while coasting so predictions don't run away
        if t_ms - self.last_update_ms > 500:
            self.x[4:6] *= math.exp(-dt / 0.6)
            self.x[2:4] *= math.exp(-dt / 2.5)  # people stop or turn while unseen: decay toward the last fix
        self.t_ms = t_ms

    def update(self, xy: tuple[float, float], t_ms: float, sigma_m: float) -> None:
        self.predict(t_ms)
        Hm = np.zeros((2, 6))
        Hm[0, 0] = Hm[1, 1] = 1.0
        R = np.eye(2) * sigma_m ** 2
        y = np.array(xy) - Hm @ self.x
        S = Hm @ self.P @ Hm.T + R
        d2 = float(y @ np.linalg.inv(S) @ y)
        if d2 > 40.0:
            R = R * (d2 / 10.0)
            S = Hm @ self.P @ Hm.T + R
        K = self.P @ Hm.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(6) - K @ Hm) @ self.P
        self.last_update_ms = t_ms
        self.updates += 1

    def peek(self, horizon_s: float) -> tuple[float, float]:
        F = self._F(horizon_s)
        x = F @ self.x
        return float(x[0]), float(x[1])

    def predicted_path(self, horizon_s: float = 2.0, step_s: float = 0.25) -> list[tuple[float, float]]:
        out = []
        t = step_s
        while t <= horizon_s + 1e-9:
            # constant-velocity extrapolation for display (acceleration extrapolation is too noisy)
            out.append((float(self.x[0] + self.x[2] * t), float(self.x[1] + self.x[3] * t)))
            t += step_s
        return out

    def stale(self, now_ms: float, after_ms: float = 700.0) -> bool:
        return now_ms - self.last_update_ms > after_ms

    @property
    def position(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[2]), float(self.x[3])

    @property
    def accel(self) -> tuple[float, float]:
        return float(self.x[4]), float(self.x[5])

    @property
    def speed(self) -> float:
        return float(math.hypot(self.x[2], self.x[3]))

    @property
    def pos_sigma(self) -> float:
        return float(math.sqrt(max(self.P[0, 0], self.P[1, 1])))
