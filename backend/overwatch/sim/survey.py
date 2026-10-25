"""Display-only camera coverage. A viewed cell does not imply a clear or safe sector."""
from __future__ import annotations

import base64
import math

import numpy as np

from .visibility import Occluders


class CameraSurvey:
    def __init__(self, world):
        x0, x1, y0, y1 = world.bounds
        self.cell = max(2., math.ceil(max(x1 - x0, y1 - y0) / 180))
        self.x0, self.y0 = x0, y0
        self.nx, self.ny = math.ceil((x1 - x0) / self.cell), math.ceil((y1 - y0) / self.cell)
        xs = x0 + (np.arange(self.nx) + .5) * self.cell
        ys = y0 + (np.arange(self.ny) + .5) * self.cell
        xx, yy = np.meshgrid(xs, ys)
        self.pts = np.column_stack((xx.ravel(), yy.ravel()))
        self.last = np.full(len(self.pts), -1e9, np.float32)
        self.occluders = Occluders(world)
        self.updated = self.cached_at = -1e9
        self.cached = None

    def update(self, t, camera):
        if t - self.updated < .5:
            return
        self.updated = t
        position = np.asarray(camera.pose.position)
        idx = np.flatnonzero(np.linalg.norm(self.pts - position[:2], axis=1) < 100)
        if not len(idx):
            return
        points = np.column_stack((self.pts[idx], np.full(len(idx), 1.1)))
        uv, depth = camera.project(points)
        inside = ((depth > .1) & (uv[:, 0] >= 0) & (uv[:, 0] < camera.intr.width) &
                  (uv[:, 1] >= 0) & (uv[:, 1] < camera.intr.height))
        idx = idx[inside]
        if len(idx):
            idx = idx[self.occluders.visible(position, self.pts[idx])]
            self.last[idx] = t
        self.cached = None

    def encode(self, t):
        if self.cached is not None and t - self.cached_at < .5:
            return self.cached
        self.cached_at = t
        age = np.where(self.last > -1e8, np.clip(t - self.last, 0, 253) + 1, 0).astype(np.uint8)
        age = age.reshape(self.ny, self.nx)
        self.cached = dict(nx=self.nx, ny=self.ny, cell=self.cell, x0=self.x0, y0=self.y0,
                           data=base64.b64encode(age[::-1].tobytes()).decode(),
                           seen_m2=int(np.count_nonzero(age) * self.cell ** 2),
                           label="Simulated camera coverage; occluded ground is not surveyed")
        return self.cached
