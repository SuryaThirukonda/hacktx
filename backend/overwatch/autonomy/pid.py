"""PID with anti-windup (conditional integration) and a first-order filtered derivative."""

from __future__ import annotations


class PID:
    def __init__(self, kp: float, ki: float = 0.0, kd: float = 0.0, limit: float = float("inf"),
                 i_limit: float | None = None, d_tau_s: float = 0.15) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.limit = limit
        self.i_limit = i_limit if i_limit is not None else limit
        self.d_tau = d_tau_s
        self.reset()

    def reset(self) -> None:
        self.i = 0.0
        self.prev_e: float | None = None
        self.d = 0.0

    def step(self, e: float, dt: float) -> float:
        if dt <= 0:
            return max(-self.limit, min(self.limit, self.kp * e + self.i))
        if self.prev_e is not None:
            raw_d = (e - self.prev_e) / dt
            a = dt / (self.d_tau + dt)
            self.d = (1 - a) * self.d + a * raw_d
        self.prev_e = e
        u_unsat = self.kp * e + self.i + self.kd * self.d
        # anti-windup: only integrate when not saturated, or when integration reduces saturation
        if abs(u_unsat) < self.limit or (u_unsat > 0) != (e > 0):
            self.i = max(-self.i_limit, min(self.i_limit, self.i + self.ki * e * dt))
        return max(-self.limit, min(self.limit, self.kp * e + self.i + self.kd * self.d))
