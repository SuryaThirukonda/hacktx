"""Static synthetic world: a training field with surveyed ArUco markers, trees, vehicles, crates and zones.

Everything here is ground truth for the simulator only; the pipeline never reads it (it sees pixels, depth and
telemetry exactly like a live deployment). Evaluation code compares pipeline output against it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

BGR = tuple[int, int, int]


@dataclass
class OpeningDecal:
    """A door, window or garage door drawn flat on a building face (ground truth carries its id and side)."""

    oid: str
    center_xy: tuple[float, float]  # on the face
    normal_xy: tuple[float, float]  # outward unit normal of the face
    width: float
    z0: float
    z1: float
    color: BGR = (40, 42, 46)


@dataclass
class BoxObj:
    """Axis-aligned (after rotation by heading) box resting on z0. Used for vehicles, crates, cardboard, benches."""

    oid: str
    kind: str
    center_xy: tuple[float, float]
    size_lwh: tuple[float, float, float]  # length along heading, width, height
    heading_deg: float = 0.0
    color: BGR = (120, 120, 120)
    z0: float = 0.0
    reflective: bool = False  # low LiDAR confidence (glass, dark paint)
    openings: list["OpeningDecal"] = field(default_factory=list)  # doors/windows drawn on the faces

    def corners(self) -> np.ndarray:
        """8x3 world corners: bottom 4 then top 4 (counter-clockwise seen from above)."""
        L, W, H = self.size_lwh
        h = math.radians(self.heading_deg)
        fwd = np.array([math.sin(h), math.cos(h)])
        right = np.array([math.cos(h), -math.sin(h)])
        c = np.array(self.center_xy)
        pts2 = [
            c + fwd * L / 2 + right * W / 2,
            c + fwd * L / 2 - right * W / 2,
            c - fwd * L / 2 - right * W / 2,
            c - fwd * L / 2 + right * W / 2,
        ]
        bottom = [np.array([p[0], p[1], self.z0]) for p in pts2]
        top = [np.array([p[0], p[1], self.z0 + H]) for p in pts2]
        return np.array(bottom + top)

    def faces(self) -> list[tuple[list[int], np.ndarray]]:
        """(vertex indices, outward normal) for the 6 faces."""
        cs = self.corners()
        idx = [
            [4, 5, 6, 7],  # top
            [0, 3, 2, 1],  # bottom
            [0, 1, 5, 4],  # front
            [1, 2, 6, 5],  # left (seen from front)
            [2, 3, 7, 6],  # back
            [3, 0, 4, 7],  # right
        ]
        out = []
        center = cs.mean(axis=0)
        for f in idx:
            p = cs[f]
            n = np.cross(p[1] - p[0], p[2] - p[0])
            nn = np.linalg.norm(n)
            n = n / nn if nn > 0 else n
            if np.dot(n, p.mean(axis=0) - center) < 0:
                n = -n
            out.append((f, n))
        return out

    def footprint_radius(self) -> float:
        L, W, _ = self.size_lwh
        return 0.5 * math.hypot(L, W)


@dataclass
class TreeObj:
    oid: str
    xy: tuple[float, float]
    trunk_r: float = 0.18
    trunk_h: float = 2.6
    canopy_r: float = 2.4
    canopy_z: float = 4.2
    color: BGR = (40, 92, 46)


@dataclass
class ConeObj:
    oid: str
    xy: tuple[float, float]
    r: float = 0.17
    h: float = 0.5
    color: BGR = (20, 110, 235)


@dataclass
class MarkerSpec:
    marker_id: int
    xy: tuple[float, float]
    size_m: float


@dataclass
class ZoneSpec:
    zone_id: str
    name: str
    polygon: list[tuple[float, float]]
    kind: str = "task"


@dataclass
class World:
    bounds: tuple[float, float, float, float] = (-25.0, 40.0, -16.0, 28.0)  # x0, x1, y0, y1
    texture_res_m: float = 0.025
    markers: list[MarkerSpec] = field(default_factory=list)
    boxes: list[BoxObj] = field(default_factory=list)
    trees: list[TreeObj] = field(default_factory=list)
    cones: list[ConeObj] = field(default_factory=list)
    zones: list[ZoneSpec] = field(default_factory=list)
    geofence: list[tuple[float, float]] = field(default_factory=list)
    sun_dir: tuple[float, float, float] = (0.45, -0.35, -0.82)  # direction light travels (towards ground)
    seed: int = 7
    marker_dictionary: str = "DICT_4X4_50"
    name: str = "training-field"
    painter: Any = None  # callable(tex, img_float_bgr, rng) -> img: paints a world's ground instead of the training field
    streets: Any = None  # street graph {nodes, edges, source}: map data for pursuit routing (None: no street map)
    launchpad: tuple[float, float] | None = None  # where a dispatched drone takes off from (and reports relative to)

    def static_ids(self) -> list[str]:
        return [b.oid for b in self.boxes] + [t.oid for t in self.trees] + [c.oid for c in self.cones]


ROOF_KINDS = ("carport", "shelter", "walkway")


def street_cover(streets: dict, boxes: list[BoxObj], trees: list[TreeObj], step_m: float = 2.0,
                 sidewalk_m: float = 5.5) -> dict[str, list[float]]:
    """Cover from above along each street, as map data would give it (tree canopy, carports, covered walkways):
    every ``step_m`` along a street, 1.0 where either sidewalk is under a canopy or a roof, else 0.0."""
    roofs = [b for b in boxes if b.kind in ROOF_KINDS]
    roof_polys = [np.array([c[:2] for c in b.corners()[:4]]) for b in roofs]
    T = np.array([t.xy for t in trees], float) if trees else np.zeros((0, 2))
    R = np.array([t.canopy_r for t in trees], float) if trees else np.zeros(0)
    out: dict[str, list[float]] = {}
    for a, b in streets["edges"]:
        A, B = np.array(streets["nodes"][a], float), np.array(streets["nodes"][b], float)
        L = float(np.linalg.norm(B - A))
        if L < 1e-6:
            continue
        u = (B - A) / L
        n = np.array([-u[1], u[0]])
        s = np.arange(0.0, L + 1e-6, step_m)
        cov = np.zeros(len(s))
        for side in (-1.0, 1.0):
            P = A[None, :] + s[:, None] * u[None, :] + side * sidewalk_m * n[None, :]
            hit = np.zeros(len(s), bool)
            if len(T):
                hit |= (np.hypot(P[:, None, 0] - T[None, :, 0], P[:, None, 1] - T[None, :, 1]) < R[None, :] + 0.5).any(axis=1)
            for poly in roof_polys:
                lo, hi = poly.min(axis=0) - 0.5, poly.max(axis=0) + 0.5
                hit |= (P[:, 0] >= lo[0]) & (P[:, 0] <= hi[0]) & (P[:, 1] >= lo[1]) & (P[:, 1] <= hi[1])
            cov = np.maximum(cov, hit.astype(float))
        out[f"{a}|{b}"] = [round(float(c), 2) for c in cov]
    return out


def launchpad_objects(xy: tuple[float, float], prefix: str = "pad") -> list[BoxObj]:
    """A landing pad (a light slab with a dark H) and a small hangar beside it."""
    x, y = xy
    dark = (58, 60, 64)
    return [BoxObj(f"{prefix}", "pad", (x, y), (5.0, 5.0, 0.08), color=(206, 208, 210)),
            BoxObj(f"{prefix}-h1", "pad_mark", (x - 1.0, y), (0.45, 2.6, 0.1), color=dark),
            BoxObj(f"{prefix}-h2", "pad_mark", (x + 1.0, y), (0.45, 2.6, 0.1), color=dark),
            BoxObj(f"{prefix}-h3", "pad_mark", (x, y), (1.6, 0.45, 0.1), color=dark),
            BoxObj(f"{prefix}-hangar", "hangar", (x - 6.5, y), (4.0, 5.0, 2.6), color=(128, 132, 138))]


def default_world(marker_layout: dict[int, tuple[float, float]] | None = None, marker_size_m: float = 1.0,
                  geofence: list[tuple[float, float]] | None = None) -> World:
    """The demo training field. Marker layout comes from config so calibration uses the same survey."""
    layout = marker_layout or {0: (0.0, 0.0), 1: (20.0, 0.0), 2: (20.0, 12.0), 3: (0.0, 12.0),
                               4: (10.0, 0.0), 5: (10.0, 12.0)}
    w = World()
    w.markers = [MarkerSpec(int(k), (float(v[0]), float(v[1])), marker_size_m) for k, v in layout.items()]
    w.boxes = [
        BoxObj("car-1", "vehicle", (28.5, 3.0), (4.6, 1.9, 1.45), heading_deg=0.0, color=(70, 70, 74), reflective=True),
        BoxObj("car-2", "vehicle", (28.5, 9.5), (4.4, 1.85, 1.5), heading_deg=0.0, color=(190, 190, 185), reflective=True),
        BoxObj("crate-1", "crate", (6.0, 6.5), (1.2, 1.0, 1.0), heading_deg=20.0, color=(96, 112, 128)),
        BoxObj("cardboard-1", "cardboard", (13.5, 4.2), (0.9, 0.7, 0.8), heading_deg=-10.0, color=(118, 140, 160)),
        BoxObj("cardboard-2", "cardboard", (3.2, 2.0), (0.6, 0.6, 0.55), heading_deg=35.0, color=(122, 146, 168)),
        BoxObj("bench-1", "bench", (-6.0, 8.0), (1.8, 0.5, 0.45), heading_deg=90.0, color=(70, 72, 76)),
    ]
    w.trees = [
        TreeObj("tree-1", (16.5, 8.0), canopy_r=2.6),
        TreeObj("tree-2", (-4.0, 15.0), canopy_r=2.9, color=(36, 86, 44)),
        TreeObj("tree-3", (-9.0, -3.0), canopy_r=2.3, color=(44, 98, 52)),
        TreeObj("tree-4", (33.0, 18.0), canopy_r=3.1, color=(38, 90, 48)),
    ]
    w.cones = []  # traffic cones read as saturated blobs to the sim detector; add per scenario if wanted
    w.zones = [
        ZoneSpec("zone-a", "Staging area", [(1.5, 1.5), (8.5, 1.5), (8.5, 6.0), (1.5, 6.0)]),
        ZoneSpec("zone-b", "Search sector B", [(12.0, 6.5), (19.0, 6.5), (19.0, 11.0), (12.0, 11.0)]),
    ]
    w.geofence = geofence or [(-18.0, -12.0), (38.0, -12.0), (38.0, 24.0), (-18.0, 24.0)]
    return w
