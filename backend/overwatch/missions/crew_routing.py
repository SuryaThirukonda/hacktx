"""Crew routing as a live service: time-dependent shortest paths for ground crews through flood water and fire.

Each search carries the crew's arrival time on every node, so a road or a cell is judged at the moment the crew
would be on it, not at the moment of planning:

    cost(edge, t_arrival) = length
                          + inf  if water_depth(edge, t_arrival) > 0.3 m              (flood: closed road)
                          + inf  if t_arrival > fire_arrival(edge) - safety_margin    (fire: keep-out)
                          + smoke_penalty * smoke(edge) * length                       (fire: smoke costs extra)

The hazard models are screening models with time compressed for the demo (they move in seconds, not hours); a
crew's walk is converted into model time with the same compression, so the comparison is consistent. The service
re-plans every time the hazard grids update (every 2 s of simulation time), publishes the routes as mission state
for the 2D map and the 3D view, and offers D9 the code-computed alternatives (unsafe ones removed before any
engine is asked). A route is a proposal for a person: nothing here moves anyone. The offline evaluators (FR2, FF1)
call the same planners and check that the live service published the same route.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

INF = math.inf
REPLAN_EVERY_MS = 2000.0
WALK_MPS = 1.25
ROAD_CLOSED_DEPTH_M = 0.3
SMOKE_PENALTY = 3.0  # a fully smoky metre costs as much as four clear ones


def flood_route(graph: Any, flood: Any, t0_s: float, start: str, goal: str, walk_mps: float = WALK_MPS,
                compression: float = 1.0, max_depth_m: float = ROAD_CLOSED_DEPTH_M,
                penalty: dict[tuple[str, str], float] | None = None) -> dict[str, Any] | None:
    """Time-dependent Dijkstra on a road graph: a road is open if the water on it, when the crew would leave it, is
    at most ``max_depth_m`` (the water only rises, so leaving is the deepest moment). ``penalty`` multiplies lengths
    (used to find alternatives). Returns {path, length_m, eta_s, max_water_depth_m, arrive_model_s} or None."""
    adj: dict[str, list[tuple[str, float]]] = {}
    for a, b in graph.edges:
        L = graph.length(a, b)
        f = (penalty or {}).get((a, b), 1.0) * (penalty or {}).get((b, a), 1.0)
        adj.setdefault(a, []).append((b, L * f))
        adj.setdefault(b, []).append((a, L * f))
    best: dict[str, float] = {start: 0.0}  # weighted cost
    walked: dict[str, float] = {start: 0.0}  # metres actually walked (for the arrival time)
    depth_at: dict[str, float] = {start: 0.0}
    prev: dict[str, str] = {}
    pq = [(0.0, start)]
    while pq:
        cu, u = heapq.heappop(pq)
        if u == goal:
            break
        if cu > best.get(u, INF):
            continue
        for v, w in adj.get(u, []):
            L = graph.length(u, v)
            t_leave = t0_s + (walked[u] + L) / walk_mps / compression
            d = graph.edge_depth(flood, u, v, t_leave) if flood is not None else 0.0
            if max_depth_m is not None and d > max_depth_m:
                continue  # closed by the time the crew would be on it
            nc = cu + w
            if nc < best.get(v, INF):
                best[v], walked[v], prev[v] = nc, walked[u] + L, u
                depth_at[v] = max(depth_at[u], d)
                heapq.heappush(pq, (nc, v))
    if goal not in best:
        return None
    path, u = [goal], goal
    while u != start:
        u = prev[u]
        path.append(u)
    path.reverse()
    L = walked[goal]
    return {"path": path, "length_m": round(L, 1), "eta_s": round(L / walk_mps, 1),
            "max_water_depth_m": round(depth_at[goal], 2), "arrive_model_s": round(t0_s + L / walk_mps / compression, 2)}


def flood_alternatives(graph: Any, flood: Any, t0_s: float, start: str, goal: str, k: int = 3,
                       walk_mps: float = WALK_MPS, compression: float = 1.0) -> list[dict[str, Any]]:
    """Up to ``k`` distinct routes for D9, closed roads allowed (so the options show why they are unsafe), each with
    the deepest water the crew would meet on it at the time they would be there."""
    out: list[dict[str, Any]] = []
    penalty: dict[tuple[str, str], float] = {}
    for _ in range(k * 3):
        r = flood_route(graph, flood, t0_s, start, goal, walk_mps, compression, max_depth_m=None, penalty=penalty)
        if r is None:
            break
        if all(r["path"] != o["path"] for o in out):
            r.update(id=f"R{len(out) + 1}", hazards_crossed=0)
            out.append(r)
            if len(out) == k:
                break
        for a, b in zip(r["path"], r["path"][1:]):
            penalty[(a, b)] = penalty.get((a, b), 1.0) * 1.8
    return out


def fire_routes(fire: Any, t0_s: float, start_xy: tuple[float, float], goals: dict[str, tuple[float, float]],
                blocked_static: np.ndarray, margin_s: float, walk_mps: float = WALK_MPS, compression: float = 1.0,
                smoke: np.ndarray | None = None, smoke_penalty: float = SMOKE_PENALTY, window_m: float = 30.0
                ) -> dict[str, dict[str, Any] | None]:
    """Time-dependent Dijkstra on the fire grid (8-connected cells) from one start to several goals at once: a cell
    is usable if the crew would be there at least ``margin_s`` (model time) before the fire arrives; smoke adds cost.
    The search stays inside the bounding box of the start and goals plus ``window_m``. Per goal: {length_m, eta_s,
    keep_out_cells, smoke_m, waypoints, arrive_model_s, cells} or None (no safe way)."""
    g = fire.grid
    arr = fire.arrival()
    s = g.cell(*start_xy)
    ends = {k: g.cell(*xy) for k, xy in goals.items()}
    rows = [s[0]] + [e[0] for e in ends.values()]
    cols = [s[1]] + [e[1] for e in ends.values()]
    pad = int(window_m / g.res_m)
    r0, r1 = max(0, min(rows) - pad), min(g.ny - 1, max(rows) + pad)
    c0, c1 = max(0, min(cols) - pad), min(g.nx - 1, max(cols) + pad)
    out: dict[str, dict[str, Any] | None] = {k: None for k in goals}
    if blocked_static[s]:
        return out
    todo = {e for e in ends.values() if not blocked_static[e]}
    best = {s: 0.0}
    walked = {s: 0.0}
    prev: dict[tuple[int, int], tuple[int, int]] = {}
    pq = [(0.0, s)]
    steps = [(dr, dc, g.res_m * math.hypot(dr, dc)) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
    while pq and todo:
        cu, u = heapq.heappop(pq)
        if cu > best.get(u, INF):
            continue
        todo.discard(u)
        wu = walked[u]
        for dr, dc, step in steps:
            v = (u[0] + dr, u[1] + dc)
            if not (r0 <= v[0] <= r1 and c0 <= v[1] <= c1) or blocked_static[v]:
                continue
            if t0_s + (wu + step) / walk_mps / compression > arr[v] - margin_s:
                continue  # the fire gets there within the margin of the crew: keep out
            nc = cu + step * (1.0 + smoke_penalty * (float(smoke[v]) if smoke is not None else 0.0))
            if nc < best.get(v, INF):
                best[v], walked[v], prev[v] = nc, wu + step, u
                heapq.heappush(pq, (nc, v))
    keep = fire.keep_out(t0_s, margin_s)
    for k, e in ends.items():
        if e not in best:
            continue
        path, u = [e], e
        while u != s:
            u = prev[u]
            path.append(u)
        path.reverse()
        L = walked[e]
        out[k] = {"length_m": round(L, 1), "eta_s": round(L / walk_mps, 1),
                  "keep_out_cells": int(sum(keep[p] for p in path)),  # against the planning-time keep-out, as before
                  "smoke_m": round(sum(float(smoke[p]) * g.res_m for p in path), 1) if smoke is not None else 0.0,
                  "waypoints": [g.centre(*p) for p in path[:: max(1, len(path) // 20)]] + [g.centre(*path[-1])],
                  "arrive_model_s": round(t0_s + L / walk_mps / compression, 2), "cells": len(path)}
    return out


@dataclass
class CrewProblem:
    """A world's crew-routing problem: who goes where, and a planner that answers for a departure time."""

    kind: str  # flood | fire
    label: str  # the hazard model's label (a screening model, not a forecast)
    plan: Callable[[float], dict[str, Any]]  # departure time (s) -> {"route": dict | None, "alternatives": [...], ...}
    crews: list[dict[str, Any]] = field(default_factory=list)


class CrewRoutingService:
    """Re-plans every hazard update, publishes the routes as mission state, offers D9 the alternatives."""

    def __init__(self, pipeline: Any) -> None:
        self.p = pipeline
        self.problem_provider: Any = None  # callable() -> CrewProblem | None (the runtime attaches the scenario's)
        self.reset()

    def reset(self) -> None:
        self.last_ms = -1e12
        self.state: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self._problem_key: Any = None

    def tick(self, now: float) -> None:
        prob = self.problem_provider() if self.problem_provider else None
        if prob is None:
            self.state = None
            return
        if now - self.last_ms < REPLAN_EVERY_MS - 1:
            return
        self.last_ms = now
        t_s = now / 1000.0
        res = prob.plan(t_s)
        route = res.get("route")
        prov = self.p._belief_provenance() if hasattr(self.p, "_belief_provenance") else "simulated"
        prev = (self.state or {}).get("route")
        changed = (prev is None) != (route is None) or (prev is not None and route is not None and
                                                        prev.get("signature") != route.get("signature"))
        self.state = {"kind": prob.kind, "label": prob.label, "ts_ms": now, "depart_s": round(t_s, 1),
                      "provenance": prov, "crews": prob.crews, **res}
        self.history.append({"ts_ms": now, "depart_s": round(t_s, 3), "route": route["signature"] if route else None})
        del self.history[:-200]
        if changed:
            self.p.outbox.append({"type": "pursuit", "kind": "crew_route_planned", "ts_ms": now, "hazard": prob.kind,
                                  "route": {k: v for k, v in (route or {}).items() if k != "waypoints"} or None,
                                  "why": res.get("why"), "provenance": prov})
            alts = res.get("alternatives") or []
            if len(alts) >= 2 and self.p.decisions is not None:
                from ..decisions.tasks import d9_route
                job = d9_route({"routes": [{k: a[k] for k in ("id", "length_m", "eta_s", "max_water_depth_m",
                                                             "hazards_crossed") if k in a} for a in alts],
                                "hazard": prob.label, "depart_s": round(t_s, 1)})
                job.context = {"engine": "rules"} if self.p.cfg.decisions.pursuit_engine == "rules" else {}
                self.p.decisions.submit(job)

    def view(self) -> dict[str, Any] | None:
        return self.state


def assessed_route(start, target, *, world, flood=None, fire=None, t_s=0., max_depth_m=.15,
                   approach_radius_m=3., fire_margin_s=120., walk_mps=None):
    """Shared off-road access assessment for SAR guidance and simulated rescue execution.

    Existing road/cell crew assignments still use their time-dependent Dijkstra
    planners. This bounded connector preserves strict approach-only/blocked results.
    With walk_mps, each edge is checked at its estimated arrival time as well.
    """
    from .sar_guidance import access_route
    return access_route(start, target, world=world, flood=flood, fire=fire, t_s=t_s,
                        max_depth_m=max_depth_m, approach_radius_m=approach_radius_m,
                        fire_margin_s=fire_margin_s, walk_mps=walk_mps)
