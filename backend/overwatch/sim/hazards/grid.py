"""A regular ground grid in local metres (x east, y north), shared by the hazard models."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Grid:
    x0: float
    y0: float
    res_m: float
    nx: int
    ny: int

    @staticmethod
    def over(bounds: tuple[float, float, float, float], res_m: float) -> "Grid":
        x0, x1, y0, y1 = bounds
        return Grid(x0, y0, res_m, int(np.ceil((x1 - x0) / res_m)), int(np.ceil((y1 - y0) / res_m)))

    def cell(self, x: float, y: float) -> tuple[int, int]:
        """(row, col) of a point; row 0 is the southern edge."""
        return (int(np.clip((y - self.y0) / self.res_m, 0, self.ny - 1)),
                int(np.clip((x - self.x0) / self.res_m, 0, self.nx - 1)))

    def centre(self, r: int, c: int) -> tuple[float, float]:
        return self.x0 + (c + 0.5) * self.res_m, self.y0 + (r + 0.5) * self.res_m

    def xy(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.res_m
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.res_m
        return np.meshgrid(xs, ys)

    def polygons(self, mask: np.ndarray, min_area_m2: float = 1.0) -> list[list[tuple[float, float]]]:
        """Outlines of a boolean mask as world polygons."""
        m = (mask.astype(np.uint8) * 255)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            if cv2.contourArea(c) * self.res_m ** 2 < min_area_m2:
                continue
            out.append([(float(self.x0 + (p[0][0] + 0.5) * self.res_m), float(self.y0 + (p[0][1] + 0.5) * self.res_m))
                        for p in c])
        return out
