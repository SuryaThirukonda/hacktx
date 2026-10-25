"""Responder-phone image orientation.

The phone uplinks an *upright* JPEG and Apple Vision boxes in upright coordinates, rotated from the sensor-native
(landscape) capture by ``image_orientation`` (CGImagePropertyOrientation naming: ``up`` = sensor-native,
``right`` = phone held upright in portrait). Depth maps and intrinsics stay sensor-native. These helpers are the
Python twin of ``ImageOrientationTracker`` / ``VisionBoxes`` in the iOS app, so the simulator exercises the same
mapping (scenario S13).
"""

from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np

ORIENTATIONS = ("up", "down", "left", "right")


def is_rotated(orientation: str | None) -> bool:
    return orientation in ("left", "right")


def sensor_point(p: tuple[float, float], orientation: str | None) -> tuple[float, float]:
    """Normalised top-left-origin point of the upright image -> sensor-native image."""
    x, y = p
    if orientation == "right":
        return y, 1.0 - x
    if orientation == "left":
        return 1.0 - y, x
    if orientation == "down":
        return 1.0 - x, 1.0 - y
    return x, y


def upright_point(s: tuple[float, float], orientation: str | None) -> tuple[float, float]:
    """Inverse of :func:`sensor_point`."""
    x, y = s
    if orientation == "right":
        return 1.0 - y, x
    if orientation == "left":
        return y, 1.0 - x
    if orientation == "down":
        return 1.0 - x, 1.0 - y
    return x, y


def rotate_to_upright(img: np.ndarray, orientation: str | None) -> np.ndarray:
    if orientation == "right":
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if orientation == "left":
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if orientation == "down":
        return cv2.rotate(img, cv2.ROTATE_180)
    return img


def orientation_from_pose(pose_ark: np.ndarray, previous: str = "right", margin: float = 0.2) -> str:
    """Which way the phone is held, from gravity in the ARKit camera pose (columns = camera axes, world y up)."""
    ux = float(pose_ark[1, 0])  # world-up component of the camera's x axis (image right)
    uy = float(pose_ark[1, 1])  # ... of its y axis (image up)
    if abs(ux) > abs(uy) + margin:
        return "right" if ux < 0 else "left"
    if abs(uy) > abs(ux) + margin:
        return "up" if uy > 0 else "down"
    return previous


def upright_hfov_deg(K: Sequence[Sequence[float]] | None, orientation: str | None, width: int, height: int,
                     fallback_hfov_deg: float) -> float:
    """Horizontal field of view of the uplinked (upright) image.

    ``K`` is the sensor-native intrinsics at any resolution (the phone sends it at depth-map resolution), so the
    FOV comes from cx/fx or cy/fy. Without ``K`` the configured FOV is taken to describe the sensor's long side.
    A missing orientation is inferred from the JPEG's aspect ratio (older app builds)."""
    rot = is_rotated(orientation) if orientation else height > width
    if K is not None:
        fx, fy, cx, cy = float(K[0][0]), float(K[1][1]), float(K[0][2]), float(K[1][2])
        if fx > 0 and fy > 0 and cx > 0 and cy > 0:
            return math.degrees(2.0 * math.atan(cy / fy)) if rot else math.degrees(2.0 * math.atan(cx / fx))
    if rot:
        short, long_ = min(width, height), max(width, height)
        return math.degrees(2.0 * math.atan(math.tan(math.radians(fallback_hfov_deg) / 2.0) * short / max(long_, 1)))
    return fallback_hfov_deg


def median_range(box_n: Sequence[float], orientation: str | None, depth: np.ndarray, conf: np.ndarray) -> float | None:
    """Median valid LiDAR depth in the torso region of an upright, normalised xyxy box (x: middle 40 %, y: 20-60 %
    from the top), sampled in the sensor-native depth map. Needs >= 4 samples with confidence >= medium."""
    x0, y0, x1, y1 = box_n
    w, h = x1 - x0, y1 - y0
    p0 = sensor_point((x0 + 0.3 * w, y0 + 0.2 * h), orientation)
    p1 = sensor_point((x1 - 0.3 * w, y0 + 0.6 * h), orientation)
    dh, dw = depth.shape
    sx0, sx1 = sorted((p0[0], p1[0]))
    sy0, sy1 = sorted((p0[1], p1[1]))
    xa = max(0, min(dw - 1, int(sx0 * dw)))
    xb = max(xa + 1, min(dw, int(sx1 * dw)))
    ya = max(0, min(dh - 1, int(sy0 * dh)))
    yb = max(ya + 1, min(dh, int(sy1 * dh)))
    patch = depth[ya:yb, xa:xb]
    vals = patch[np.isfinite(patch) & (patch > 0) & (conf[ya:yb, xa:xb] >= 1)]
    return round(float(np.median(vals)), 2) if vals.size >= 4 else None
