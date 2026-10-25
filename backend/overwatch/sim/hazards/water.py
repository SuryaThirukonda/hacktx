"""Flood water screening model and road closures.

Water level rises linearly from ``level0_m`` at ``rise_m_per_min`` up to ``level_max_m`` over a ground elevation grid;
depth = level - ground. A road edge is closed when the water on it exceeds 0.3 m (the plan's limit for vehicles).
This is a bathtub model (no flow, no momentum): a fast screening approximation for the simulator, not a forecast.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from .grid import Grid

ROAD_CLOSED_DEPTH_M = 0.3
LABEL = "simulated flood (bathtub screening model, not a forecast)"


@dataclass
class FloodModel:
    grid: Grid
    ground_m: np.ndarray  # ny x nx elevations
    level0_m: float
    rise_m_per_min: float
    level_max_m: float

    def level(self, t_s: float) -> float:
        return min(self.level_max_m, self.level0_m + self.rise_m_per_min * t_s / 60.0)

    def depth_grid(self, t_s: float) -> np.ndarray:
        return np.clip(self.level(t_s) - self.ground_m, 0.0, None)

    def depth(self, x: float, y: float, t_s: float) -> float:
        r, c = self.grid.cell(x, y)
        return float(max(0.0, self.level(t_s) - self.ground_m[r, c]))

    def flooded_polygons(self, t_s: float, min_depth_m: float = 0.05) -> list[list[tuple[float, float]]]:
        return self.grid.polygons(self.depth_grid(t_s) >= min_depth_m, min_area_m2=4.0)


@dataclass
class RoadGraph:
    nodes: dict[str, tuple[float, float]]
    edges: list[tuple[str, str]] = field(default_factory=list)

    def edge_depth(self, flood: FloodModel, a: str, b: str, t_s: float, step_m: float = 2.0) -> float:
        (ax, ay), (bx, by) = self.nodes[a], self.nodes[b]
        n = max(2, int(math.hypot(bx - ax, by - ay) / step_m))
        return max(flood.depth(ax + (bx - ax) * i / n, ay + (by - ay) * i / n, t_s) for i in range(n + 1))

    def length(self, a: str, b: str) -> float:
        (ax, ay), (bx, by) = self.nodes[a], self.nodes[b]
        return math.hypot(bx - ax, by - ay)


def shortest(graph: RoadGraph, flood: FloodModel | None, t_s: float, start: str, goal: str,
             max_depth_m: float | None = ROAD_CLOSED_DEPTH_M, penalty: dict[tuple[str, str], float] | None = None
             ) -> dict | None:
    """Dijkstra over open roads (depth <= max_depth_m). Returns {path, length_m, max_water_depth_m} or None."""
    adj: dict[str, list[tuple[str, float, float]]] = {}
    for a, b in graph.edges:
        d = graph.edge_depth(flood, a, b, t_s) if flood is not None else 0.0
        if max_depth_m is not None and d > max_depth_m:
            continue
        w = graph.length(a, b) * (penalty or {}).get((a, b), 1.0) * (penalty or {}).get((b, a), 1.0)
        adj.setdefault(a, []).append((b, w, d))
        adj.setdefault(b, []).append((a, w, d))
    dist = {start: 0.0}
    prev: dict[str, tuple[str, float]] = {}
    pq = [(0.0, start)]
    while pq:
        du, u = heapq.heappop(pq)
        if u == goal:
            break
        if du > dist.get(u, math.inf):
            continue
        for v, w, d in adj.get(u, []):
            nd = du + w
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, (u, d)
                heapq.heappush(pq, (nd, v))
    if goal not in dist:
        return None
    path, depth, u = [goal], 0.0, goal
    while u != start:
        p, d = prev[u]
        depth = max(depth, d)
        path.append(p)
        u = p
    path.reverse()
    length = sum(graph.length(a, b) for a, b in zip(path, path[1:]))
    return {"path": path, "length_m": round(length, 1), "max_water_depth_m": round(depth, 2)}


def candidate_routes(graph: RoadGraph, flood: FloodModel, t_s: float, start: str, goal: str, k: int = 3,
                     walk_mps: float = 1.25) -> list[dict]:
    """Up to ``k`` distinct routes (closed roads allowed, so the options can show why they are unsafe), each with its
    computed length, walking time and deepest water. Code removes unsafe ones before any decision engine sees them."""
    out: list[dict] = []
    penalty: dict[tuple[str, str], float] = {}
    for i in range(k * 3):
        r = shortest(graph, flood, t_s, start, goal, max_depth_m=None, penalty=penalty)
        if r is None:
            break
        if all(r["path"] != o["path"] for o in out):
            r.update(id=f"R{len(out) + 1}", eta_s=round(r["length_m"] / walk_mps, 1), hazards_crossed=0)
            out.append(r)
            if len(out) == k:
                break
        for a, b in zip(r["path"], r["path"][1:]):
            penalty[(a, b)] = penalty.get((a, b), 1.0) * 1.8
    return out
