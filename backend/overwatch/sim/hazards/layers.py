"""Hazard layers for a scenario: per-pixel image overlays for the drone camera and polygons for the map.

Colours are kept below the simulated detector's saturation threshold so water, ash and smoke do not read as people.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .fire import LABEL as FIRE_LABEL
from .fire import FireModel
from .water import LABEL as WATER_LABEL
from .water import ROAD_CLOSED_DEPTH_M, FloodModel, RoadGraph, shortest

WATER_BGR = np.array([118, 104, 86], np.float32)  # muddy blue-grey (saturation about 70 of 255)
ASH_BGR = np.array([60, 62, 82], np.float32)
SMOKE_BGR = np.array([176, 178, 180], np.float32)


def _sample(grid, arr: np.ndarray, xs: np.ndarray, ys: np.ndarray, hit: np.ndarray, fill: float = 0.0) -> np.ndarray:
    r = np.clip(((ys - grid.y0) / grid.res_m).astype(int), 0, grid.ny - 1)
    c = np.clip(((xs - grid.x0) / grid.res_m).astype(int), 0, grid.nx - 1)
    inside = hit & (xs >= grid.x0) & (ys >= grid.y0) & (xs < grid.x0 + grid.nx * grid.res_m) & \
        (ys < grid.y0 + grid.ny * grid.res_m)
    out = np.full(xs.shape, fill, np.float32)
    out[inside] = arr[r[inside], c[inside]]
    return out


class FloodLayers:
    label = WATER_LABEL
    covers_people = False

    def __init__(self, flood: FloodModel, roads: RoadGraph | None = None, start: str = "staging",
                 goal: str | None = None) -> None:
        self.flood = flood
        self.roads = roads
        self.start = start
        self.goal = goal
        self._cache_bin: int | None = None
        self._cache_3d: dict[str, Any] = {}

    def overlay(self, xs: np.ndarray, ys: np.ndarray, hit: np.ndarray, t_s: float) -> tuple[np.ndarray, np.ndarray]:
        xs, ys = np.nan_to_num(xs), np.nan_to_num(ys)
        depth = _sample(self.flood.grid, self.flood.depth_grid(t_s), xs, ys, hit)
        alpha = np.where(depth > 0.02, np.clip(0.35 + depth * 0.6, 0, 0.82), 0.0).astype(np.float32)
        return np.repeat(WATER_BGR[None, :], len(xs), 0), alpha

    def layers(self, t_s: float) -> dict[str, Any]:
        return {"label": self.label, "level_m": round(self.flood.level(t_s), 2),
                "areas": [{"kind": "water", "polygon": p} for p in self.flood.flooded_polygons(t_s)]}

    def world_3d(self, step: int = 4) -> dict[str, Any]:
        """Static elevation grid and flood parameters for the 3D scene."""
        g = self.flood.grid
        z = self.flood.ground_m[::step, ::step]
        ny, nx = z.shape
        return {
            "kind": "flood",
            "label": self.label,
            "grid": {"x0": g.x0, "y0": g.y0, "nx": nx, "ny": ny, "res_m": g.res_m * step},
            "ground_m": [round(float(v), 3) for v in z.ravel()],
            "level0_m": self.flood.level0_m,
            "rise_m_per_min": self.flood.rise_m_per_min,
            "level_max_m": self.flood.level_max_m,
            "road_closed_depth_m": ROAD_CLOSED_DEPTH_M,
        }

    def frame_3d(self, t_s: float) -> dict[str, Any]:
        """Dynamic flood water level, shoreline/deep-water polygons, closed roads and safe crew route for /3d."""
        b = int(round(t_s * 2.0))
        if b != self._cache_bin:
            self._cache_bin = b
            t_bin = b / 2.0
            dgrid = self.flood.depth_grid(t_bin)
            cell_area = self.flood.grid.res_m ** 2
            areas = ([{"kind": "water", "polygon": p} for p in self.flood.flooded_polygons(t_bin, 0.05)] +
                     [{"kind": "deep_water", "polygon": p}
                      for p in self.flood.flooded_polygons(t_bin, ROAD_CLOSED_DEPTH_M)])
            closed_roads = []
            crew_route = None
            if self.roads is not None:
                for a, nb in self.roads.edges:
                    ed = self.roads.edge_depth(self.flood, a, nb, t_bin)
                    if ed > ROAD_CLOSED_DEPTH_M:
                        closed_roads.append({"a": list(self.roads.nodes[a]), "b": list(self.roads.nodes[nb]),
                                             "depth_m": round(ed, 2)})
                if self.goal and self.start in self.roads.nodes and self.goal in self.roads.nodes:
                    r = shortest(self.roads, self.flood, t_bin, self.start, self.goal)
                    if r is not None:
                        crew_route = {
                            "waypoints": [list(self.roads.nodes[n]) for n in r["path"]],
                            "length_m": r["length_m"],
                            "max_water_depth_m": r["max_water_depth_m"],
                            "goal": self.goal,
                        }
            self._cache_3d = {
                "flooded_area_m2": round(float((dgrid >= 0.05).sum() * cell_area), 0),
                "deep_area_m2": round(float((dgrid >= ROAD_CLOSED_DEPTH_M).sum() * cell_area), 0),
                "areas": areas,
                "closed_roads": closed_roads,
                "crew_route": crew_route,
            }
        lvl = self.flood.level(t_s)
        max_d = max(0.0, lvl - float(self.flood.ground_m.min()))
        return {
            "kind": "flood",
            "label": self.label,
            "level_m": round(lvl, 3),
            "max_depth_m": round(max_d, 2),
            "road_closed_depth_m": ROAD_CLOSED_DEPTH_M,
            **self._cache_3d,
        }


class FireLayers:
    label = FIRE_LABEL
    covers_people = True  # smoke hides people; that is part of the scenario

    def __init__(self, fire: FireModel, keep_out_margin_s: float = 120.0, building: Any = None,
                 staging: tuple[float, float] | None = None, route_fn: Any = None) -> None:
        self.fire = fire
        self.margin = keep_out_margin_s
        self.building = building
        self.staging = staging
        self.route_fn = route_fn
        self._cache_bin: int | None = None
        self._cache_3d: dict[str, Any] = {}

    def overlay(self, xs: np.ndarray, ys: np.ndarray, hit: np.ndarray, t_s: float) -> tuple[np.ndarray, np.ndarray]:
        xs, ys = np.nan_to_num(xs), np.nan_to_num(ys)
        g = self.fire.grid
        burn = _sample(g, self.fire.burning(t_s).astype(np.float32), xs, ys, hit)
        smoke = _sample(g, self.fire.smoke(t_s).astype(np.float32), xs, ys, hit)
        a_burn = burn * 0.8
        a_smoke = smoke * 0.75
        alpha = np.clip(a_burn + a_smoke * (1 - a_burn), 0, 0.9)
        w = np.where(alpha > 0, a_burn / np.maximum(alpha, 1e-6), 0.0)[:, None]
        color = ASH_BGR[None, :] * w + SMOKE_BGR[None, :] * (1 - w)
        return color, alpha.astype(np.float32)

    def layers(self, t_s: float) -> dict[str, Any]:
        return {"label": self.label,
                "areas": [{"kind": "fire", "polygon": p} for p in self.fire.burning_polygons(t_s)]
                + [{"kind": "keep_out", "polygon": p} for p in self.fire.keep_out_polygons(t_s, self.margin)]
                + [{"kind": "smoke", "polygon": p} for p in self.fire.grid.polygons(self.fire.smoke(t_s) > 0.25, 9.0)]}

    def world_3d(self, step: int = 2) -> dict[str, Any]:
        """Static fire arrival grid and wind/spread parameters for the 3D scene."""
        g = self.fire.grid
        arr = self.fire.arrival()[::step, ::step]
        ny, nx = arr.shape
        return {
            "kind": "fire",
            "label": self.label,
            "grid": {"x0": g.x0, "y0": g.y0, "nx": nx, "ny": ny, "res_m": g.res_m * step},
            "arrival_s": [round(float(v), 2) if np.isfinite(v) else -1.0 for v in arr.ravel()],
            "ignitions": [list(ig) for ig in self.fire.ignitions],
            "wind_to_deg": self.fire.wind_to_deg,
            "wind_speed_mps": self.fire.wind_speed_mps,
            "keep_out_margin_s": self.margin,
        }

    def frame_3d(self, t_s: float) -> dict[str, Any]:
        """Dynamic burning/keep-out/smoke state, plume source, building openings and safe crew route for /3d."""
        b = int(round(t_s * 2.0))
        if b != self._cache_bin:
            self._cache_bin = b
            t_bin = b / 2.0
            g = self.fire.grid
            burn = self.fire.burning(t_bin)
            keep = self.fire.keep_out(t_bin, self.margin)
            smoke = self.fire.smoke(t_bin)
            cell_area = g.res_m ** 2
            smoke_src = None
            smoke_str = 0.0
            if burn.any():
                rr, cc = np.nonzero(burn)
                sx = g.x0 + (float(cc.mean()) + 0.5) * g.res_m
                sy = g.y0 + (float(rr.mean()) + 0.5) * g.res_m
                smoke_src = [round(sx, 2), round(sy, 2)]
                smoke_str = round(min(1.0, float(burn.sum()) * cell_area / 400.0), 3)
            openings = []
            crew_route = None
            if self.building is not None:
                for op in self.building.openings:
                    ox, oy = self.building.opening_xy(op)
                    (_, _), (nx, ny), _ = self.building.face(op.side)
                    r, c = g.cell(ox + nx * 2.0, oy + ny * 2.0)
                    sm = float(smoke[r, c])
                    ko = bool(keep[r, c])
                    openings.append({
                        "id": op.id, "side": op.side, "kind": op.kind,
                        "xy": [round(ox, 2), round(oy, 2)],
                        "smoke": round(sm, 2), "in_keep_out": ko,
                        "condition": "unclear" if sm > 0.25 else "no_obstruction_seen",
                    })
                target = next((o for o in openings if not o["in_keep_out"] and o["kind"] == "door"), None)
                if target is not None and self.staging is not None and self.route_fn is not None:
                    op = next(o for o in self.building.openings if o.id == target["id"])
                    ox, oy = self.building.opening_xy(op)
                    (_, _), (nx, ny), _ = self.building.face(op.side)
                    rt = self.route_fn(self.fire, t_bin, self.staging, (ox + nx * 2.5, oy + ny * 2.5), self.margin)
                    if rt is not None:
                        crew_route = {
                            "target_id": target["id"],
                            "length_m": rt["length_m"],
                            "keep_out_cells": rt["keep_out_cells"],
                            "waypoints": [list(w) for w in rt["waypoints"]],
                        }
            self._cache_3d = {
                "burning_area_m2": round(float(burn.sum() * cell_area), 0),
                "keep_out_area_m2": round(float(keep.sum() * cell_area), 0),
                "smoke_source_xy": smoke_src,
                "smoke_strength": smoke_str,
                "areas": self.layers(t_bin)["areas"],
                "openings": openings,
                "crew_route": crew_route,
            }
        return {
            "kind": "fire",
            "label": self.label,
            "wind_to_deg": self.fire.wind_to_deg,
            "wind_speed_mps": self.fire.wind_speed_mps,
            "keep_out_margin_s": self.margin,
            **self._cache_3d,
        }
