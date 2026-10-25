"""Map-based SIMULATED walking guidance. No LiDAR, actor distress labels or aircraft commands."""
from __future__ import annotations

import heapq
import math
from functools import lru_cache
from itertools import pairwise


def in_grid(grid, x: float, y: float) -> bool:
    return grid.x0 <= x < grid.x0 + grid.nx * grid.res_m and grid.y0 <= y < grid.y0 + grid.ny * grid.res_m


def access_route(start, target, *, flood=None, fire=None, world=None, t_s=0.0,
                 max_depth_m=0.15, approach_radius_m=3.0, fire_margin_s=120.0, walk_mps=None) -> dict:
    """A* over a bounded 1 m grid, with every edge sampled at <=0.5 m.

    Never snaps the responder across a hazard. Inaccessible targets get an approach
    point only if one is reachable within the stated radius. No path => no direct arrow.
    """
    out = {"status": "unavailable", "points": [], "length_m": None, "max_water_depth_m": None,
           "depth_limit_m": max_depth_m, "navigation_source": "simulated hazard map and structure geometry"}
    if flood is None and fire is None and world is None:
        return {**out, "reason": "no simulated navigation/hazard map"}
    if not all(math.isfinite(float(v)) for v in (*start, *target)):
        return {**out, "reason": "invalid coordinates"}
    arrival = fire.arrival() if fire is not None else None

    @lru_cache(maxsize=60000)
    def safe(x, y, arrival_s=0.0):
        at_s = t_s + arrival_s
        if world is not None:
            x0, x1, y0, y1 = world.bounds
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                return False
            for box in world.boxes:
                if box.size_lwh[2] < 0.15:
                    continue
                h = math.radians(box.heading_deg)
                dx, dy = x - box.center_xy[0], y - box.center_xy[1]
                along = dx * math.sin(h) + dy * math.cos(h)
                across = dx * math.cos(h) - dy * math.sin(h)
                if abs(along) < box.size_lwh[0] / 2 + .5 and abs(across) < box.size_lwh[1] / 2 + .5:
                    return False
        if flood is not None and (not in_grid(flood.grid, x, y) or flood.depth(x, y, at_s) > max_depth_m):
            return False
        if fire is not None:
            if not in_grid(fire.grid, x, y):
                return False
            r, c = fire.grid.cell(x, y)
            if arrival[r, c] <= at_s + fire_margin_s:
                return False
        return True

    def segment(a, b, walked=0.0):
        n = max(1, math.ceil(math.dist(a, b) / .5))
        return all(safe(round(a[0] + (b[0] - a[0]) * i / n, 4),
                        round(a[1] + (b[1] - a[1]) * i / n, 4),
                        (walked + math.dist(a, b) * i / n) / walk_mps if walk_mps else 0.0) for i in range(n + 1))

    if not safe(*start):
        return {**out, "status": "blocked", "reason": "responder is not on assessed traversable ground"}
    # Grid is anchored at the exact responder position, so the initial connector cannot jump a blocked cell.
    sx, sy = map(float, start)
    tx, ty = map(float, target)
    goal_safe = safe(tx, ty, math.dist(start, target) / walk_mps if walk_mps else 0.0)
    padding = 25.0
    lo_x, hi_x = min(sx, tx) - padding, max(sx, tx) + padding
    lo_y, hi_y = min(sy, ty) - padding, max(sy, ty) + padding
    start_cell = (0, 0)
    queue = [(math.dist(start, target), 0.0, start_cell)]
    costs, parent = {start_cell: 0.0}, {}
    end = None
    visited = 0
    while queue and visited < 30000:
        _, cost, cell = heapq.heappop(queue)
        if cost != costs.get(cell):
            continue
        visited += 1
        x, y = sx + cell[0], sy + cell[1]
        distance = math.hypot(x - tx, y - ty)
        if (goal_safe and distance <= 1.0 and segment((x, y), (tx, ty), cost)) or (
                not goal_safe and distance <= approach_radius_m):
            end = cell
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cell[0] + dx, cell[1] + dy)
            nx, ny = sx + nxt[0], sy + nxt[1]
            if not (lo_x <= nx <= hi_x and lo_y <= ny <= hi_y) or cost + 1 >= costs.get(nxt, float("inf")):
                continue
            if not segment((x, y), (nx, ny), cost):
                continue
            costs[nxt], parent[nxt] = cost + 1, cell
            heapq.heappush(queue, (cost + 1 + max(0, math.hypot(nx - tx, ny - ty) - approach_radius_m), cost + 1, nxt))
    if end is None:
        return {**out, "status": "blocked", "reason": "no assessed access route; specialist rescue/access review needed"}
    cells = [end]
    while cells[-1] != start_cell:
        cells.append(parent[cells[-1]])
    points = [(sx + c[0], sy + c[1]) for c in reversed(cells)]
    if goal_safe and points[-1] != (tx, ty):
        points.append((tx, ty))
    max_water = max((flood.depth(x, y, t_s) for x, y in points), default=0.0) if flood else 0.0
    return {**out, "status": "access_route" if goal_safe else "approach_only",
            "points": [[round(x, 3), round(y, 3)] for x, y in points],
            "length_m": round(sum(math.dist(a, b) for a, b in pairwise(points)), 1),
            "max_water_depth_m": round(float(max_water), 3),
            "target_accessible": goal_safe, "approach_distance_m": round(math.dist(points[-1], target), 1),
            "reason": "assessed simulated access" if goal_safe else "stop at approach point; target remains in a hazard/obstacle"}
