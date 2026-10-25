"""Log-odds occupancy grid fed only by registered LiDAR rays (Rescue Copilot §10.4).

Cells are unknown until a LiDAR ray observes them. Rays carve free space up to the obstacle endpoint; the drone
detector never marks space free. Evidence decays so stale obstacles fade instead of persisting as facts.
"""

from __future__ import annotations

import base64

import cv2
import numpy as np


class OccupancyGrid:
    L_HIT = 0.85
    L_MISS = -0.35
    L_MIN, L_MAX = -2.5, 3.5

    def __init__(self, origin_xy: tuple[float, float], size_xy: tuple[float, float], cell_m: float) -> None:
        self.origin = np.array(origin_xy, float)
        self.cell = cell_m
        self.nx = int(round(size_xy[0] / cell_m))
        self.ny = int(round(size_xy[1] / cell_m))
        self.L = np.zeros((self.ny, self.nx), np.float32)
        self.seen = np.zeros((self.ny, self.nx), bool)
        self.updates = 0

    def reset(self) -> None:
        self.L[:] = 0
        self.seen[:] = False
        self.updates = 0

    def _idx(self, xy: np.ndarray) -> np.ndarray:
        ij = np.floor((np.asarray(xy, float) - self.origin) / self.cell).astype(int)
        return ij  # (ix, iy)

    def decay(self, dt_s: float, tau_s: float = 20.0) -> None:
        if dt_s > 0:
            self.L *= float(np.exp(-dt_s / tau_s))

    def integrate(self, sensor_xy: tuple[float, float], points_xy: np.ndarray) -> None:
        if len(points_xy) == 0:
            return
        s = self._idx(np.array([sensor_xy]))[0]
        ends = self._idx(points_xy)
        ok = (ends[:, 0] >= 0) & (ends[:, 0] < self.nx) & (ends[:, 1] >= 0) & (ends[:, 1] < self.ny)
        ends = np.unique(ends[ok], axis=0)
        if len(ends) == 0:
            return
        # free space along rays (rasterised as anti-aliased-free lines on a miss mask)
        miss = np.zeros((self.ny, self.nx), np.uint8)
        for ex, ey in ends:
            cv2.line(miss, (int(s[0]), int(s[1])), (int(ex), int(ey)), 1, 1)
        hit = np.zeros_like(miss)
        hit[ends[:, 1], ends[:, 0]] = 1
        miss[hit > 0] = 0
        self.L[miss > 0] += self.L_MISS
        self.L[hit > 0] += self.L_HIT
        np.clip(self.L, self.L_MIN, self.L_MAX, out=self.L)
        self.seen |= (miss > 0) | (hit > 0)
        self.updates += 1

    def probability(self) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.L))

    def encode_png_b64(self) -> str:
        """Grayscale PNG (row 0 = north edge): 0 = unknown, 1..127 = free, 128..255 = occupied probability."""
        p = self.probability()
        img = np.where(self.seen, np.clip(p * 254 + 1, 1, 255), 0).astype(np.uint8)
        img = np.flipud(img)
        ok, buf = cv2.imencode(".png", img)
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""

    def meta(self) -> dict:
        return {"origin_xy": self.origin.tolist(), "cell_m": self.cell, "nx": self.nx, "ny": self.ny,
                "updates": self.updates}

    def occupied_cells(self, thresh: float = 0.7) -> np.ndarray:
        p = self.probability()
        iy, ix = np.nonzero((p > thresh) & self.seen)
        return np.column_stack([self.origin[0] + (ix + 0.5) * self.cell, self.origin[1] + (iy + 0.5) * self.cell])
