"""The street network as arrays: directed segments with their sidewalk lanes, turns, nearest-point lookup and shortest
paths. The runner and the search particles move on the public streets' sidewalks (always the right-hand one for
their direction of travel); the police car drives the centre lines and may use the station's driveway.
"""

from __future__ import annotations

import heapq
import math

import numpy as np

from .world import LANE_OFFSET


class Streets:
    def __init__(self, graph: dict, public_only: bool) -> None:
        edges = [e for e in graph["edges"] if not public_only or e["kind"] == "street"]
        used = sorted({e["a"] for e in edges} | {e["b"] for e in edges})
        self.names = used
        self.idx = {n: i for i, n in enumerate(used)}
        self.xy = np.array([graph["nodes"][n] for n in used], float)
        self.labels = [graph["labels"].get(n, n) for n in used]
        self.und = [(self.idx[e["a"]], self.idx[e["b"]]) for e in edges]
        self.und_name = [e["name"] for e in edges]
        n = len(self.und)
        # directed segment 2k runs a->b of undirected segment k, 2k+1 runs b->a
        self.frm = np.zeros(2 * n, int)
        self.to = np.zeros(2 * n, int)
        for k, (a, b) in enumerate(self.und):
            self.frm[2 * k], self.to[2 * k], self.frm[2 * k + 1], self.to[2 * k + 1] = a, b, b, a
        d = self.xy[self.to] - self.xy[self.frm]
        self.length = np.linalg.norm(d, axis=1)
        self.dir = d / np.maximum(self.length, 1e-9)[:, None]
        self.right = np.stack([self.dir[:, 1], -self.dir[:, 0]], axis=1)
        self.lane = np.array([LANE_OFFSET[e["axis"]] for e in edges for _ in (0, 1)], float)
        self.out: list[list[int]] = [[] for _ in range(len(used))]
        for e in range(2 * n):
            self.out[self.frm[e]].append(e)
        self.max_out = max(len(o) for o in self.out)
        self.out_arr = np.full((len(used), self.max_out), -1, int)
        for i, o in enumerate(self.out):
            self.out_arr[i, :len(o)] = o

    @staticmethod
    def rev(e: int | np.ndarray):
        return e ^ 1

    def lane_xy(self, e, s) -> np.ndarray:
        """A point on the right-hand sidewalk of directed segment(s) ``e`` at ``s`` metres along."""
        e = np.asarray(e)
        s = np.asarray(s, float)
        return self.xy[self.frm[e]] + self.dir[e] * s[..., None] + self.right[e] * self.lane[e][..., None]

    def centre_xy(self, e, s) -> np.ndarray:
        e = np.asarray(e)
        return self.xy[self.frm[e]] + self.dir[e] * np.asarray(s, float)[..., None]

    def corner(self, e_in: int, e_out: int) -> np.ndarray:
        """Where the right-hand sidewalk of ``e_in`` meets the right-hand sidewalk of ``e_out`` at their shared node."""
        node = self.xy[self.to[e_in]]
        if e_out == self.rev(e_in):
            return node + self.right[e_in] * self.lane[e_in]
        if abs(float(np.dot(self.dir[e_in], self.dir[e_out]))) > 0.9:  # straight on
            return node + self.right[e_in] * self.lane[e_in]
        return node + self.right[e_in] * self.lane[e_in] + self.right[e_out] * self.lane[e_out]

    def turns(self, e: int) -> list[int]:
        """The directed segments one can continue on from the end of ``e`` (a U-turn only at a dead end)."""
        nxt = [o for o in self.out[self.to[e]] if o != self.rev(e)]
        return nxt or [self.rev(e)]

    def project(self, xy, lane: bool = True) -> tuple[int, float, float]:
        """Nearest directed segment to ``xy`` (on its right-hand sidewalk when ``lane``): (segment, s, distance)."""
        p = np.asarray(xy, float)
        a = self.xy[self.frm] + (self.right * self.lane[:, None] if lane else 0.0)
        t = np.clip(np.einsum("ij,ij->i", p - a, self.dir), 0.0, self.length)
        q = a + self.dir * t[:, None]
        dist = np.linalg.norm(q - p, axis=1)
        e = int(np.argmin(dist))
        return e, float(t[e]), float(dist[e])

    def nearest_node(self, xy) -> int:
        return int(np.argmin(np.linalg.norm(self.xy - np.asarray(xy, float), axis=1)))

    def place_label(self, xy) -> str:
        """'Oak St & Main St' for a point near an intersection, else 'Oak St near Main St'."""
        e, _, _ = self.project(xy, lane=False)
        street = self.und_name[e // 2]
        cross = [i for i in range(len(self.xy)) if "&" in self.labels[i]]
        i = min(cross, key=lambda i: math.dist(self.xy[i], xy))
        if math.dist(self.xy[i], xy) < 22.0:
            return self.labels[i]
        other = self.labels[i].replace(street, "").strip(" &")
        return f"{street} near {other}" if other else street

    # ---------------------------------------------------------------------------------------------------------
    def distances_from(self, xy) -> np.ndarray:
        """Shortest street distance from a point (projected onto the network) to every node (Dijkstra)."""
        e, s, _ = self.project(xy, lane=False)
        dist = np.full(len(self.xy), np.inf)
        heap = [(s, int(self.frm[e])), (self.length[e] - s, int(self.to[e]))]
        while heap:
            d, i = heapq.heappop(heap)
            if d >= dist[i]:
                continue
            dist[i] = d
            for o in self.out[i]:
                j = int(self.to[o])
                nd = d + float(self.length[o])
                if nd < dist[j]:
                    heapq.heappush(heap, (nd, j))
        return dist

    def route(self, start_xy, goal_xy) -> list[np.ndarray]:
        """Centre-line waypoints from ``start_xy`` to ``goal_xy`` along the streets (both projected onto them)."""
        es, ss, _ = self.project(start_xy, lane=False)
        eg, sg, _ = self.project(goal_xy, lane=False)
        p_start = self.centre_xy(es, ss)
        p_goal = self.centre_xy(eg, sg)
        if es // 2 == eg // 2:  # same street segment
            return [p_start, p_goal]
        dist = {int(self.frm[es]): (ss, None), int(self.to[es]): (self.length[es] - ss, None)}
        heap = [(d, i) for i, (d, _) in dist.items()]
        heapq.heapify(heap)
        best: dict[int, tuple[float, int | None]] = {}
        while heap:
            d, i = heapq.heappop(heap)
            if i in best:
                continue
            best[i] = (d, dist[i][1])
            for o in self.out[i]:
                j = int(self.to[o])
                nd = d + float(self.length[o])
                if j not in best and (j not in dist or nd < dist[j][0]):
                    dist[j] = (nd, i)
                    heapq.heappush(heap, (nd, j))
        ends = [(best[int(self.frm[eg])][0] + sg, int(self.frm[eg])) if int(self.frm[eg]) in best else (np.inf, -1),
                (best[int(self.to[eg])][0] + self.length[eg] - sg, int(self.to[eg])) if int(self.to[eg]) in best else (np.inf, -1)]
        _, last = min(ends)
        chain = []
        i: int | None = last
        while i is not None:
            chain.append(i)
            i = best[i][1]
        return [p_start] + [self.xy[i].copy() for i in reversed(chain)] + [p_goal]


def path_length(pts: list[np.ndarray]) -> float:
    return float(sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:])))
