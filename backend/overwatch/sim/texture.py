"""Procedural ground texture (top-down orthophoto) for the synthetic field: mown grass, paths, tape, ArUco markers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .world import World


@dataclass
class GroundTexture:
    image: np.ndarray  # BGR uint8, rows = north->south
    res_m: float
    x0: float
    y1: float

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]

    def world_from_tex(self) -> np.ndarray:
        """3x3 mapping texture pixel (col, row, 1) -> world (X, Y, 1)."""
        r = self.res_m
        return np.array([[r, 0.0, self.x0 + 0.5 * r], [0.0, -r, self.y1 - 0.5 * r], [0.0, 0.0, 1.0]])

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.x0) / self.res_m - 0.5, (self.y1 - y) / self.res_m - 0.5

    def downsampled(self, res_m: float) -> GroundTexture:
        f = self.res_m / res_m
        img = cv2.resize(self.image, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        return GroundTexture(img, res_m, self.x0, self.y1)


def _value_noise(h: int, w: int, cell: int, rng: np.random.Generator) -> np.ndarray:
    gh, gw = max(2, h // cell + 2), max(2, w // cell + 2)
    small = rng.random((gh, gw)).astype(np.float32)
    big = cv2.resize(small, (gw * cell, gh * cell), interpolation=cv2.INTER_CUBIC)
    return big[:h, :w]


def build_ground_texture(world: World) -> GroundTexture:
    x0, x1, y0, y1 = world.bounds
    res = world.texture_res_m
    w = int(round((x1 - x0) / res))
    h = int(round((y1 - y0) / res))
    rng = np.random.default_rng(world.seed)
    tex = GroundTexture(np.zeros((h, w, 3), np.uint8), res, x0, y1)

    # --- grass: base colour, multi-octave brightness noise, mowing stripes, blade speckle -------------------
    base = np.array([58, 128, 74], np.float32)  # BGR
    n = (0.45 * _value_noise(h, w, 400, rng) + 0.35 * _value_noise(h, w, 90, rng) + 0.2 * _value_noise(h, w, 18, rng))
    shade = 0.82 + 0.32 * n
    xs = x0 + (np.arange(w) + 0.5) * res
    stripes = np.where((np.floor((xs + 100.0) / 3.0) % 2) == 0, 1.06, 0.95).astype(np.float32)
    shade *= stripes[None, :]
    speckle = rng.normal(0.0, 0.05, (h, w)).astype(np.float32)
    shade += cv2.GaussianBlur(speckle, (3, 3), 0)
    hue_shift = (_value_noise(h, w, 220, rng) - 0.5) * 22.0
    img = np.empty((h, w, 3), np.float32)
    img[..., 0] = base[0] * shade - hue_shift * 0.4
    img[..., 1] = base[1] * shade
    img[..., 2] = base[2] * shade + hue_shift

    # --- dry / dirt patches (low saturation browns) -------------------------------------------------------
    for _ in range(9):
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        rad = rng.uniform(1.2, 3.5)
        px, py = tex.to_px(cx, cy)
        mask = np.zeros((h, w), np.float32)
        cv2.ellipse(mask, (int(px), int(py)), (int(rad / res), int(rad * rng.uniform(0.5, 1.0) / res)),
                    rng.uniform(0, 180), 0, 360, 1.0, -1, cv2.LINE_AA)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(1.0, 0.6 / res))
        mask *= 0.55 * (0.6 + 0.4 * _value_noise(h, w, 30, rng))
        dirt = np.array([92, 118, 128], np.float32)
        img = img * (1 - mask[..., None]) + dirt[None, None, :] * shade[..., None] * mask[..., None]

    if world.painter is not None:  # another world paints its own roads, water and yards
        img = world.painter(tex, img, rng)
        img = np.clip(img, 0, 255).astype(np.uint8)
        tex.image = img
        return tex

    # --- concrete footpath (polyline) ---------------------------------------------------------------------
    path = [(-25.0, -6.0), (-5.0, -5.0), (12.0, -4.0), (24.0, -3.0), (26.0, 6.0), (26.5, 28.0)]
    pts = np.array([tex.to_px(*p) for p in path], np.int32)
    pmask = np.zeros((h, w), np.uint8)
    cv2.polylines(pmask, [pts], False, 255, int(2.2 / res), cv2.LINE_AA)
    pm = (pmask.astype(np.float32) / 255.0)[..., None]
    concrete = (np.array([168, 172, 170], np.float32)[None, None, :]
                * (0.9 + 0.2 * _value_noise(h, w, 12, rng))[..., None])
    img = img * (1 - pm) + concrete * pm
    # expansion joints
    for i in range(len(path) - 1):
        (ax, ay), (bx, by) = path[i], path[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        for s in np.arange(0, seg, 2.0):
            cx, cy = ax + (bx - ax) * s / seg, ay + (by - ay) * s / seg
            nx, ny = -(by - ay) / seg, (bx - ax) / seg
            p1 = tex.to_px(cx + nx * 1.05, cy + ny * 1.05)
            p2 = tex.to_px(cx - nx * 1.05, cy - ny * 1.05)
            cv2.line(img, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), (128, 132, 130), 1, cv2.LINE_AA)

    # --- parking pad for the vehicles -------------------------------------------------------------------------
    pad = [(25.5, -1.0), (31.5, -1.0), (31.5, 13.0), (25.5, 13.0)]
    ppts = np.array([tex.to_px(*p) for p in pad], np.int32)
    asphalt = np.zeros((h, w), np.uint8)
    cv2.fillPoly(asphalt, [ppts], 255)
    am = (asphalt.astype(np.float32) / 255.0)[..., None]
    asph = np.array([72, 74, 76], np.float32)[None, None, :] * (0.9 + 0.2 * _value_noise(h, w, 8, rng))[..., None]
    img = img * (1 - am) + asph * am
    for y in (-1.0, 6.0, 13.0):
        a, b = tex.to_px(25.6, y), tex.to_px(31.4, y)
        cv2.line(img, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (225, 225, 225), max(1, int(0.1 / res)), cv2.LINE_AA)

    img = np.clip(img, 0, 255).astype(np.uint8)

    # --- tape: task zones outlined in white tape ---------------------------------------------------------
    for z in world.zones:
        zp = np.array([tex.to_px(*p) for p in z.polygon], np.int32)
        cv2.polylines(img, [zp], True, (232, 236, 236), max(1, int(0.06 / res)), cv2.LINE_AA)

    # --- ArUco markers (top edge faces local +y), white quiet zone ------------------------------------------
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, world.marker_dictionary))
    for m in world.markers:
        side_px = int(round(m.size_m / res))
        marker = cv2.aruco.generateImageMarker(dictionary, m.marker_id, side_px)
        quiet = int(round(0.12 / res))
        tile = np.full((side_px + 2 * quiet, side_px + 2 * quiet), 245, np.uint8)
        tile[quiet:quiet + side_px, quiet:quiet + side_px] = np.where(marker > 127, 245, 18)
        cx, cy = tex.to_px(*m.xy)
        top, left = int(round(cy - tile.shape[0] / 2)), int(round(cx - tile.shape[1] / 2))
        t0, l0 = max(top, 0), max(left, 0)
        t1, l1 = min(top + tile.shape[0], h), min(left + tile.shape[1], w)
        if t1 > t0 and l1 > l0:
            img[t0:t1, l0:l1] = cv2.cvtColor(tile[t0 - top:t1 - top, l0 - left:l1 - left], cv2.COLOR_GRAY2BGR)

    tex.image = img
    return tex


def marker_world_corners(xy: tuple[float, float], size_m: float) -> np.ndarray:
    """World corners in ArUco order (TL, TR, BR, BL) for a marker laid with its top edge toward +y."""
    cx, cy = xy
    s = size_m / 2.0
    return np.array([[cx - s, cy + s], [cx + s, cy + s], [cx + s, cy - s], [cx - s, cy - s]], dtype=float)
