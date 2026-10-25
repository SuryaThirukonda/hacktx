"""The police car: parked at the station until the drone's first position report, then driven along the streets to
the reported position, timed to get there about 20 s after it set off, re-routing on every new report. After that it
keeps closing on the latest callout at street speed, stops beside the suspect and sets off again when they run.
Once on its way it pings its own position (its GPS: where it is, its heading and speed) to the drone every second;
the drone's callouts are relative to that.
"""

from __future__ import annotations

import math

import numpy as np

from overwatch.geometry.camera import heading_from_vector, wrap_deg

from .streets import Streets, path_length

CATCH_UP_S = 20.0
LANE_M = 2.0  # drives on the right-hand lane


class PoliceCar:
    def __init__(self, streets: Streets, home_xy, heading: float = 90.0) -> None:
        self.st = streets
        self.xy = np.array(home_xy, float)  # centre-line position
        self.heading = heading
        self.speed = 0.0
        self.route: list[np.ndarray] = []
        self.state = "PARKED"  # PARKED | EN_ROUTE | CLOSING | STOPPED
        self.dispatched_t: float | None = None
        self.arrive_by: float | None = None
        self.arrived_t: float | None = None
        self.goal: np.ndarray | None = None
        self.distance_m = 0.0
        self.vel = np.zeros(2)  # its actual motion (what a GPS reports as course and speed over ground)
        self._side = np.zeros(2)

    def dispatch(self, t: float, goal_xy) -> None:
        self.dispatched_t = t
        self.arrive_by = t + CATCH_UP_S
        self.state = "EN_ROUTE"
        self.replan(goal_xy)

    def replan(self, goal_xy) -> None:
        if self.state == "PARKED":
            return
        self.goal = np.asarray(goal_xy, float)
        pts = self.st.route(self.xy, self.goal)
        self.route = [p for p in pts[1:]]
        if self.state == "STOPPED" and self.route and float(np.linalg.norm(self.route[-1] - self.xy)) > 8.0:
            self.state = "CLOSING"  # the suspect moved on: follow the new report

    def eta_s(self, t: float) -> float | None:
        if self.state == "EN_ROUTE" and self.arrive_by is not None:
            return max(0.0, self.arrive_by - t)
        return None

    def step(self, t: float, dt: float) -> None:
        if self.state in ("PARKED", "STOPPED") or not self.route:
            self.speed = 0.0
            self.vel = np.zeros(2)
            return
        remaining = path_length([self.xy] + self.route)
        if self.state == "EN_ROUTE" and self.arrive_by is not None and t < self.arrive_by - 0.5:
            want = remaining / max(self.arrive_by - t, 1.0)
        else:  # closing on a suspect who kept running: quickly while far, gently when near
            want = min(14.0, 0.8 * remaining)
            if self.state == "EN_ROUTE":
                self.state = "CLOSING"
                self.arrived_t = t
        if remaining < 3.0:
            want = 0.0
        want = float(np.clip(want, 0.0, 26.0))
        self.speed += float(np.clip(want - self.speed, -7.0 * dt, 5.0 * dt))
        travel = self.speed * dt
        moved = np.zeros(2)
        while travel > 1e-6 and self.route:
            to = self.route[0] - self.xy
            d = float(np.linalg.norm(to))
            if d <= travel:
                self.xy = self.route.pop(0).astype(float)
                moved += to
                travel -= d
            else:
                self.xy = self.xy + to / d * travel
                moved += to / d * travel
                travel = 0.0
        self.distance_m += float(np.linalg.norm(moved))
        self.vel = moved / dt if dt > 0 else np.zeros(2)
        if float(np.linalg.norm(moved)) > 1e-3:
            want_h = heading_from_vector(*moved)
            dh = wrap_deg(want_h - self.heading)
            self.heading = (self.heading + math.copysign(min(abs(dh), 120.0 * dt), dh)) % 360.0
        if (not self.route or remaining < 3.0) and self.speed < 0.3:
            self.state = "STOPPED"
            self.speed = 0.0

    def ping(self, t: float) -> dict:
        """The car's radio ping: its own position (on its lane, as its GPS has it), the way it faces, and its course
        and speed over ground."""
        x, y, h = self.pose()
        return {"t": t, "xy": (x, y), "heading": h, "speed": float(np.linalg.norm(self.vel)),
                "vel": (float(self.vel[0]), float(self.vel[1]))}

    def pose(self) -> tuple[float, float, float]:
        """Where to draw it: on the right-hand lane of its direction of travel."""
        h = math.radians(self.heading)
        right = np.array([math.cos(h), -math.sin(h)])
        if self.state != "PARKED":
            self._side = right * LANE_M
        p = self.xy + self._side
        return float(p[0]), float(p[1]), float(self.heading)
