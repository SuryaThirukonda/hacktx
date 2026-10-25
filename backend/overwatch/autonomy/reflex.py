"""Obstacle awareness and the safety reflex for the low-altitude (jev-drone) stack.

The ideas are from the jev-drone project (MIT): a compact symbolic scene of a few forward range sectors, a reflex in
plain code that always owns safety, and a judgment model asked only when something is in the way. That code flew a
MuJoCo quadrotor from its own depth camera; here the sectors come from the simulator's geometry (a stand-in for a
depth camera: the same numbers a depth sensor would give, with no noise). Nothing here reads where the people are.

* ``scene_sectors``: five forward range sectors at the aircraft's height and the same sectors a climb higher, the
  nearest obstacle, how much of the path ahead is free, and the top of what blocks it.
* ``reflex``: brake for what is in the path (not for what is beside it), steer towards the wider side, back off
  when something is very close. It runs at the command rate and overrides every other command, including a
  judgment model's maneuver.
* ``decision_needed``: code decides when a judgment is worth asking for (something close, or sectors blocked).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

N_SECTORS = 5
FOV_DEG = 60.0
MAX_RANGE_M = 25.0
ABOVE_M = 3.5  # how much higher the "climb" sectors look
BRAKE_M = 6.0  # start braking when the path ahead is shorter than this
BACK_OFF_M = 2.5  # back away when something is this close
STEER_MPS = 1.8
CONSULT_WITHIN_M = 8.0  # ask for a judgment only when something is this close ahead
SECTOR_NAMES = ("left2", "left1", "ahead", "right1", "right2")


def _ray_circle(o: np.ndarray, d: np.ndarray, c: np.ndarray, r: float) -> float | None:
    """Distance along the ray (o + t d, unit d) to a circle, or None."""
    oc = o - c
    b = float(np.dot(oc, d))
    cc = float(np.dot(oc, oc)) - r * r
    disc = b * b - cc
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    if t < 0:
        t = -b + math.sqrt(disc)
    return t if t >= 0 else None


def _ray_polygon(o: np.ndarray, d: np.ndarray, poly: np.ndarray) -> float | None:
    """Distance along the ray to a convex polygon's edges (the first hit), or None."""
    best = None
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        e = b - a
        denom = d[0] * e[1] - d[1] * e[0]
        if abs(denom) < 1e-9:
            continue
        ao = a - o
        t = (ao[0] * e[1] - ao[1] * e[0]) / denom
        u = (ao[0] * d[1] - ao[1] * d[0]) / denom
        if t >= 0 and 0 <= u <= 1 and (best is None or t < best):
            best = t
    return best


def _obstacles_at(boxes: Iterable[Any], trees: Iterable[Any], xy: np.ndarray, z: float, margin: float,
                  max_range: float) -> list[tuple[str, Any, float]]:
    """Obstacles that span height ``z`` (with a margin for the airframe) within range: ("poly", corners, top) or
    ("circle", (centre, radius), top)."""
    out: list[tuple[str, Any, float]] = []
    for b in boxes:
        top = b.z0 + b.size_lwh[2]
        if z + margin < b.z0 or z - margin > top:
            continue
        c = np.array(b.center_xy, float)
        if float(np.linalg.norm(c - xy)) > max_range + max(b.size_lwh[:2]):
            continue
        out.append(("poly", b.corners()[:4, :2], top))
    for t in trees:
        c = np.array(t.xy, float)
        if float(np.linalg.norm(c - xy)) > max_range + t.canopy_r:
            continue
        top = t.canopy_z + t.canopy_r
        dz = z - t.canopy_z
        if abs(dz) < t.canopy_r + margin:  # inside the canopy's height band: a circle of the sphere's section
            r = math.sqrt(max(0.0, t.canopy_r ** 2 - max(0.0, abs(dz) - margin) ** 2))
            out.append(("circle", (c, max(r, 0.3)), top))
        elif z - margin < t.trunk_h:
            out.append(("circle", (c, t.trunk_r), top))
    return out


def _ranges(obstacles: list[tuple[str, Any, float]], o: np.ndarray, heading_deg: float, n: int, fov: float,
            max_range: float) -> tuple[list[float], list[float | None]]:
    ranges: list[float] = []
    tops: list[float | None] = []
    for i in range(n):
        bearing = math.radians(heading_deg - fov / 2 + (i + 0.5) * fov / n)
        d = np.array([math.sin(bearing), math.cos(bearing)])
        best, top = max_range, None
        for kind, geom, t_top in obstacles:
            t = _ray_polygon(o, d, geom) if kind == "poly" else _ray_circle(o, d, geom[0], geom[1])
            if t is not None and t < best:
                best, top = t, t_top
        ranges.append(round(float(best), 2))
        tops.append(top)
    return ranges, tops


def scene_sectors(boxes: Iterable[Any], trees: Iterable[Any], pos_xyz: tuple[float, float, float], heading_deg: float,
                  n: int = N_SECTORS, fov_deg: float = FOV_DEG, max_range: float = MAX_RANGE_M, margin: float = 0.8,
                  above_m: float = ABOVE_M) -> dict[str, Any]:
    """The symbolic scene a depth camera would give: forward range sectors at the aircraft's height and higher."""
    xy = np.array(pos_xyz[:2], float)
    z = float(pos_xyz[2])
    boxes, trees = list(boxes), list(trees)
    level = _obstacles_at(boxes, trees, xy, z, margin, max_range)
    above = _obstacles_at(boxes, trees, xy, z + above_m, margin, max_range)
    ranges, tops = _ranges(level, xy, heading_deg, n, fov_deg, max_range)
    ranges_up, _ = _ranges(above, xy, heading_deg, n, fov_deg, max_range)
    mid = n // 2
    path = min(ranges[max(0, mid - 1):mid + 2])  # what is in the path: the middle sectors, not what is beside
    top_ahead = [t for t in tops[max(0, mid - 1):mid + 2] if t is not None]
    return {"sector_range_m": dict(zip(SECTOR_NAMES[:n], ranges)), "sector_range_above_m": dict(zip(SECTOR_NAMES[:n], ranges_up)),
            "nearest_obstacle_m": min(ranges), "path_ahead_m": path, "free_ahead_level_m": ranges[mid],
            "free_ahead_above_m": ranges_up[mid], "sectors_blocked": sum(1 for r in ranges if r < BRAKE_M),
            "obstacle_top_m": round(max(top_ahead), 1) if top_ahead else None,
            "clearance_left_m": round(min(ranges[:mid]), 2), "clearance_right_m": round(min(ranges[mid + 1:]), 2),
            "altitude_m": round(z, 1), "max_range_m": max_range,
            "source": "simulator geometry (depth-camera stand-in, no noise)"}


def nearest_obstacle_m(boxes: Iterable[Any], trees: Iterable[Any], pos_xyz: tuple[float, float, float],
                       max_range: float = 12.0) -> float:
    """Omnidirectional: how close the aircraft is to anything at its height (for the evaluation's clearance)."""
    xy = np.array(pos_xyz[:2], float)
    best = max_range
    for kind, geom, _ in _obstacles_at(list(boxes), list(trees), xy, float(pos_xyz[2]), 0.0, max_range):
        if kind == "circle":
            d = float(np.linalg.norm(xy - geom[0])) - geom[1]
        else:
            poly = geom
            d = min(_point_segment(xy, poly[i], poly[(i + 1) % 4]) for i in range(4))
            if _inside(poly, xy):
                d = 0.0
        best = min(best, max(0.0, d))
    return round(best, 2)


def _point_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / max(float(np.dot(ab, ab)), 1e-9), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def _inside(poly: np.ndarray, p: np.ndarray) -> bool:
    sign = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        s = c >= 0
        if sign is None:
            sign = s
        elif s != sign:
            return False
    return True


@dataclass
class ReflexResult:
    forward: float
    right: float
    up: float
    yaw_rate: float
    active: bool = False
    reason: str | None = None


def wider_side(scene: dict[str, Any]) -> float:
    """-1 for left, +1 for right: the side with more free space."""
    return -1.0 if scene["clearance_left_m"] >= scene["clearance_right_m"] else 1.0


def reflex(forward: float, right: float, up: float, yaw_rate: float, scene: dict[str, Any] | None,
           brake_m: float = BRAKE_M, back_off_m: float = BACK_OFF_M, steer_mps: float = STEER_MPS) -> ReflexResult:
    """The code's veto on every command: nothing flies into what the sectors show."""
    if not scene:
        return ReflexResult(forward, right, up, yaw_rate)
    ahead = float(scene["path_ahead_m"])
    side = wider_side(scene)
    if ahead < back_off_m:
        return ReflexResult(-1.0, side * steer_mps, max(up, 0.0), yaw_rate, True, f"backing off: {ahead:.1f} m ahead")
    if ahead < brake_m:
        k = (ahead - back_off_m) / (brake_m - back_off_m)  # 0 at back-off range, 1 at the brake range
        fwd = min(forward, max(0.0, forward) * 0.35 * k)
        return ReflexResult(fwd, right + side * steer_mps * (1.0 - 0.5 * k), up, yaw_rate, True,
                            f"braking: {ahead:.1f} m ahead, steering {'left' if side < 0 else 'right'}")
    return ReflexResult(forward, right, up, yaw_rate)


def decision_needed(scene: dict[str, Any] | None) -> bool:
    """A judgment is worth paying for only when something is actually in the way."""
    return bool(scene) and (float(scene["path_ahead_m"]) < CONSULT_WITHIN_M or int(scene["sectors_blocked"]) >= 2)
