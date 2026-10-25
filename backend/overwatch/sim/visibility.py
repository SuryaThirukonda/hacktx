"""Map-geometry camera line of sight. Hidden ground is never counted as surveyed."""
from __future__ import annotations

import numpy as np

from .world import World

PERSON_Z = 1.1


class Occluders:
    def __init__(self, world: World) -> None:
        boxes = []
        for b in world.boxes:
            top = b.z0 + b.size_lwh[2]
            if top < 1.2 or b.kind in ("pad", "pad_mark", "post"):
                continue
            cs = b.corners()
            boxes.append((cs[:, 0].min(), cs[:, 0].max(), cs[:, 1].min(), cs[:, 1].max(), b.z0, top))
        self.box = np.array(boxes, float).reshape(-1, 6)
        self.tree = np.array([(t.xy[0], t.xy[1], t.canopy_z, t.canopy_r * .92)
                              for t in world.trees], float).reshape(-1, 4)

    def visible(self, cam_pos: np.ndarray, pts_xy: np.ndarray, z: float = PERSON_Z) -> np.ndarray:
        """Clear segments from the camera to the supplied ground/torso points."""
        points = np.column_stack([pts_xy, np.full(len(pts_xy), z)])
        if not len(points):
            return np.zeros(0, bool)
        camera = np.asarray(cam_pos, float)
        delta = points - camera
        lo = np.minimum(points[:, :2].min(axis=0), camera[:2]) - 6
        hi = np.maximum(points[:, :2].max(axis=0), camera[:2]) + 6
        ok = np.ones(len(points), bool)
        boxes = self.box
        near = ((boxes[:, 1] >= lo[0]) & (boxes[:, 0] <= hi[0]) &
                (boxes[:, 3] >= lo[1]) & (boxes[:, 2] <= hi[1]))
        boxes = boxes[near]
        if len(boxes):
            t0 = np.full((len(points), len(boxes)), -np.inf)
            t1 = np.full_like(t0, np.inf)
            for axis, (low, high) in enumerate(((0, 1), (2, 3), (4, 5))):
                parallel = np.abs(delta[:, axis:axis + 1]) < 1e-9
                inverse = 1 / np.where(parallel, 1, delta[:, axis:axis + 1])
                a = (boxes[None, :, low] - camera[axis]) * inverse
                b = (boxes[None, :, high] - camera[axis]) * inverse
                inside = (camera[axis] >= boxes[None, :, low]) & (camera[axis] <= boxes[None, :, high])
                axis_lo = np.where(parallel, np.where(inside, -np.inf, np.inf), np.minimum(a, b))
                axis_hi = np.where(parallel, np.where(inside, np.inf, -np.inf), np.maximum(a, b))
                t0, t1 = np.maximum(t0, axis_lo), np.minimum(t1, axis_hi)
            ok &= ~((t1 >= np.maximum(t0, 0)) & (t0 < .985)).any(axis=1)
        trees = self.tree
        if len(trees):
            near = ((trees[:, 0] + trees[:, 3] >= lo[0]) & (trees[:, 0] - trees[:, 3] <= hi[0]) &
                    (trees[:, 1] + trees[:, 3] >= lo[1]) & (trees[:, 1] - trees[:, 3] <= hi[1]))
            trees = trees[near]
            if len(trees):
                rel = trees[:, :3] - camera
                dd = np.einsum("nk,nk->n", delta, delta)[:, None]
                t = np.clip(np.einsum("nk,mk->nm", delta, rel) / np.maximum(dd, 1e-9), 0, .985)
                closest = camera[None, None, :] + t[..., None] * delta[:, None, :]
                distance2 = np.sum((closest - trees[None, :, :3]) ** 2, axis=2)
                ok &= ~(distance2 < trees[None, :, 3] ** 2).any(axis=1)
        return ok
