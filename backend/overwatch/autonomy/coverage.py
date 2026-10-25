"""Coverage paths over a search sector (boustrophedon / lawnmower), idea after multi-robot-coverage, reimplemented.

Track spacing comes from the camera footprint and the overlap wanted. The path is shown to the pilot and flown by
the scripted pilot in the simulator. Real flight stays manual, and this module never sends commands.
"""

from __future__ import annotations

import math

import numpy as np


def footprint_width_m(alt_m: float, hfov_deg: float, pitch_deg: float = -90.0) -> float:
    """Across-track ground width seen by a camera at ``alt_m`` (nadir approximation for steep pitches)."""
    tilt = math.radians(90.0 + pitch_deg)  # 0 for straight down
    slant = alt_m / max(0.2, math.cos(tilt))
    return 2.0 * slant * math.tan(math.radians(hfov_deg / 2.0))


def lawnmower(polygon: list[tuple[float, float]], spacing_m: float, heading_deg: float = 0.0) -> list[tuple[float, float]]:
    """Waypoints of back-and-forth passes covering a convex polygon, passes along ``heading_deg``."""
    h = math.radians(heading_deg)
    along = np.array([math.sin(h), math.cos(h)])
    across = np.array([math.cos(h), -math.sin(h)])
    pts = np.array(polygon, float)
    c_proj = pts @ across
    out: list[tuple[float, float]] = []
    lo_c, hi_c = float(c_proj.min()), float(c_proj.max())
    n = max(1, math.ceil((hi_c - lo_c) / spacing_m - 1e-9))  # passes no wider apart than requested
    for k in range(n):
        c = lo_c + (k + 0.5) * (hi_c - lo_c) / n
        hits = []  # intersect the line {across = c} with the polygon edges
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            cp, cq = p @ across, q @ across
            if (cp - c) * (cq - c) <= 0 and cp != cq:
                s = (c - cp) / (cq - cp)
                hits.append(float((p + s * (q - p)) @ along))
        if len(hits) >= 2:
            seg = [min(hits), max(hits)] if k % 2 == 0 else [max(hits), min(hits)]
            for a in seg:
                v = a * along + c * across
                out.append((round(float(v[0]), 2), round(float(v[1]), 2)))
    return out


def path_length(wps: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(wps, wps[1:]))
