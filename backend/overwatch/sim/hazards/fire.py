"""Fire spread and smoke screening models.

Arrival times are shortest paths over a rate-of-spread grid (the approach used by ember-style fast fire models):
travel time between neighbouring cells = distance / spread rate in that direction, with an elliptical wind factor.
Non-burnable cells (roads, water, bare ground) stop spread. Smoke is a steady Gaussian plume downwind of the
burning area. Both are fast approximations for the simulator, labelled as such, never forecasts.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from .grid import Grid

LABEL = "simulated fire spread and smoke (screening models, not forecasts)"


@dataclass
class FireModel:
    grid: Grid
    fuel: np.ndarray  # ny x nx, 1 = burnable, 0 = not
    ignitions: list[tuple[float, float, float]]  # (x, y, t_s)
    base_ros_mps: float = 0.08
    wind_speed_mps: float = 3.0
    wind_to_deg: float = 45.0  # direction the wind blows towards, degrees clockwise from north
    _arrival: np.ndarray | None = field(default=None, repr=False)

    def _ros(self, dx: float, dy: float) -> float:
        """Spread rate along (dx, dy): faster downwind (simple elliptical factor), never below 20% of base."""
        wx, wy = math.sin(math.radians(self.wind_to_deg)), math.cos(math.radians(self.wind_to_deg))
        n = math.hypot(dx, dy) or 1.0
        align = (dx * wx + dy * wy) / n
        return self.base_ros_mps * max(0.2, 1.0 + 0.35 * self.wind_speed_mps * align)

    def arrival(self) -> np.ndarray:
        if self._arrival is not None:
            return self._arrival
        g = self.grid
        t = np.full((g.ny, g.nx), np.inf)
        pq: list[tuple[float, int, int]] = []
        for x, y, t0 in self.ignitions:
            r, c = g.cell(x, y)
            if self.fuel[r, c] and t0 < t[r, c]:
                t[r, c] = t0
                heapq.heappush(pq, (t0, r, c))
        steps = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
        while pq:
            tu, r, c = heapq.heappop(pq)
            if tu > t[r, c]:
                continue
            for dr, dc in steps:
                rr, cc = r + dr, c + dc
                if not (0 <= rr < g.ny and 0 <= cc < g.nx) or not self.fuel[rr, cc]:
                    continue
                d = g.res_m * math.hypot(dr, dc)
                nt = tu + d / self._ros(dc * g.res_m, dr * g.res_m)
                if nt < t[rr, cc]:
                    t[rr, cc] = nt
                    heapq.heappush(pq, (nt, rr, cc))
        self._arrival = t
        return t

    def burning(self, t_s: float) -> np.ndarray:
        return self.arrival() <= t_s

    def keep_out(self, t_s: float, margin_s: float = 120.0) -> np.ndarray:
        """Cells the fire reaches within ``margin_s`` from now (or already burning)."""
        return self.arrival() <= t_s + margin_s

    def smoke(self, t_s: float, spread_deg: float = 18.0) -> np.ndarray:
        """Relative smoke density in [0, 1] per cell from the burning area (Gaussian plume downwind)."""
        burn = self.burning(t_s)
        g = self.grid
        if not burn.any():
            return np.zeros((g.ny, g.nx))
        rr, cc = np.nonzero(burn)
        sx, sy = g.x0 + (cc.mean() + 0.5) * g.res_m, g.y0 + (rr.mean() + 0.5) * g.res_m
        X, Y = g.xy()
        wx, wy = math.sin(math.radians(self.wind_to_deg)), math.cos(math.radians(self.wind_to_deg))
        along = (X - sx) * wx + (Y - sy) * wy
        across = -(X - sx) * wy + (Y - sy) * wx
        sigma = np.maximum(3.0, np.abs(along) * math.tan(math.radians(spread_deg)))
        dens = np.where(along > -5.0, np.exp(-0.5 * (across / sigma) ** 2) * np.exp(-np.maximum(along, 0) / 150.0), 0.0)
        strength = min(1.0, burn.sum() * g.res_m ** 2 / 400.0)
        return np.clip(dens * strength, 0, 1)

    def burning_polygons(self, t_s: float) -> list[list[tuple[float, float]]]:
        return self.grid.polygons(self.burning(t_s), min_area_m2=4.0)

    def keep_out_polygons(self, t_s: float, margin_s: float = 120.0) -> list[list[tuple[float, float]]]:
        return self.grid.polygons(self.keep_out(t_s, margin_s), min_area_m2=4.0)
