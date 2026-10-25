"""Flood rescue world (Search and routing): a low-lying neighbourhood with rising water h(t), a road graph with
elevations (closed above 0.3 m of water), stranded people, debris and a ground crew.

Scale: 500 by 500 m, the plan's size, around the original 180 m core (FR1 and FR2 happen there). The water model is a bathtub screening model with time compressed for the demo (it rises
over seconds, not hours) and is labelled as such. Everything is simulated and labelled SIMULATED.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..actors import Wait
from ..hazards.grid import Grid
from ..hazards.layers import FloodLayers
from ..hazards.water import FloodModel, RoadGraph
from ..world import BoxObj, TreeObj, World, ZoneSpec, launchpad_objects
from .scoring import actor_found, false_tracks

BOUNDS = (-250.0, 250.0, -250.0, 250.0)
ROADS_Y = (-130.0, -30.0, 10.0, 50.0, 130.0, 210.0)
ROADS_X = (-200.0, -50.0, 0.0, 50.0, 200.0)
STAGING = (0.0, 82.0)


def ground() -> tuple[Grid, np.ndarray]:
    """Elevation: rises to the north, with a low channel along the south (where the creek overflows)."""
    g = Grid.over(BOUNDS, 1.0)
    X, Y = g.xy()
    z = 0.011 * (Y + 90.0) + 0.15 * np.sin(X / 23.0) * np.cos(Y / 31.0)
    z -= 0.9 * np.exp(-((Y + 62.0) / 12.0) ** 2)
    return g, z


def flood_model() -> FloodModel:
    g, z = ground()
    return FloodModel(g, z, level0_m=0.15, rise_m_per_min=1.2, level_max_m=1.1)  # time compressed for the demo


# --- the static flood (FL1): water that is already there and stays there ------------------------------------------
STATIC_LEVEL_M = 0.75  # the south half of the neighbourhood is under water; the north and a few mounds stay dry
ISLANDS = [(-62.0, -56.0, 15.0), (12.0, -72.0, 13.0), (58.0, -42.0, 14.0)]  # (x, y, radius): raised ground
FLOOD_PAD = (0.0, 96.0)  # the drone's pad beside the staging area
STRANDED = {"stranded-a": (12.0, -70.0), "stranded-b": (-61.0, -55.0), "stranded-c": (57.0, -41.0)}  # fixed, on the islands


def static_ground() -> tuple[Grid, np.ndarray]:
    """The same ground with three raised mounds: dry islands inside the flooded blocks."""
    g, z = ground()
    X, Y = g.xy()
    for cx, cy, r in ISLANDS:
        z = z + 1.6 * np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / (r * r)))
    return g, z


def static_flood_model() -> FloodModel:
    """No rise: the level is fixed, so the run does not depend on how long the drone takes."""
    g, z = static_ground()
    return FloodModel(g, z, level0_m=STATIC_LEVEL_M, rise_m_per_min=0.0, level_max_m=STATIC_LEVEL_M)


def roads() -> RoadGraph:
    nodes = {f"{x:+.0f},{y:+.0f}": (x, y) for x in ROADS_X for y in ROADS_Y}
    nodes["staging"] = STAGING
    edges = []
    for x in ROADS_X:
        for a, b in zip(ROADS_Y, ROADS_Y[1:]):
            edges.append((f"{x:+.0f},{a:+.0f}", f"{x:+.0f},{b:+.0f}"))
    for y in ROADS_Y:
        for a, b in zip(ROADS_X, ROADS_X[1:]):
            edges.append((f"{a:+.0f},{y:+.0f}", f"{b:+.0f},{y:+.0f}"))
    edges.append(("staging", "+0,+50"))
    # a low road along the creek to the south-west yards
    nodes["sw-yards"] = (-50.0, -58.0)
    nodes["se-yards"] = (50.0, -58.0)
    edges += [("-50,-30", "sw-yards"), ("+50,-30", "se-yards"), ("sw-yards", "se-yards")]
    return RoadGraph(nodes, edges)


def _paint(tex, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    res = tex.res_m
    h, w = img.shape[:2]
    lawn = np.array([60, 116, 84], np.float32)
    img = img * 0.4 + lawn[None, None, :] * 0.6
    segs = [((x, BOUNDS[2]), (x, BOUNDS[3])) for x in ROADS_X] + [((BOUNDS[0], y), (BOUNDS[1], y)) for y in ROADS_Y] + \
        [((-50.0, -30.0), (-50.0, -58.0)), ((50.0, -30.0), (50.0, -58.0)), ((-50.0, -58.0), (50.0, -58.0)),
         ((0.0, 50.0), STAGING)]
    img = np.ascontiguousarray(img, dtype=np.float32)
    for a, b in segs:
        pa, pb = tex.to_px(*a), tex.to_px(*b)
        cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (82, 84, 86), max(1, int(6.5 / res)), cv2.LINE_AA)
    return img


def build_world() -> World:
    w = World(bounds=BOUNDS, texture_res_m=0.1, seed=41)
    w.name = "flood"
    w.painter = _paint
    rng = np.random.default_rng(41)
    w.boxes = []
    i = 0
    for bx in (-75.0, -25.0, 25.0, 75.0):
        for by in (-46.0, -10.0, 30.0, 70.0):
            for dx in (-10.0, 10.0):
                i += 1
                shade = int(rng.integers(104, 132))
                w.boxes.append(BoxObj(f"house-{i}", "house", (bx + dx, by + 4.0), (9.0, 8.0, 5.5),
                                      color=(shade, shade + 2, shade + 6)))
    for bx in np.arange(-225.0, 230.0, 50.0):  # the wider neighbourhood around the core
        for by in np.arange(-210.0, 240.0, 40.0):
            if abs(bx) < 105 and abs(by) < 95:
                continue
            for dx in (-10.0, 10.0):
                x, y = float(bx + dx), float(by + 4.0)
                if any(abs(x - rx) < 9 for rx in ROADS_X) or any(abs(y - ry) < 9 for ry in ROADS_Y):
                    continue
                i += 1
                shade = int(rng.integers(104, 132))
                w.boxes.append(BoxObj(f"house-{i}", "house", (x, y), (9.0, 8.0, 5.5), color=(shade, shade + 2, shade + 6)))
    w.boxes += [BoxObj(f"debris-{k}", "debris", (float(x), float(y)), (1.4, 0.8, 0.3), heading_deg=float(a),
                       color=(84, 92, 102)) for k, (x, y, a) in enumerate([(-30, -55, 20), (12, -63, 70), (38, -52, 5)])]
    w.trees = [TreeObj(f"yard-tree-{k}", (float(x), float(y)), canopy_r=2.6, color=(40, 92, 50))
               for k, (x, y) in enumerate([(-62, -20), (-12, 22), (36, -18), (62, 40), (-38, 60)])]
    w.zones = [ZoneSpec("fr-low", "Low-lying blocks", [(-90, -90), (90, -90), (90, -20), (-90, -20)], kind="sector")]
    w.geofence = [(-245, -245), (245, -245), (245, 245), (-245, 245)]
    w.launchpad = FLOOD_PAD
    w.boxes += launchpad_objects(FLOOD_PAD, "flood-pad")
    return w


TIME_COMPRESSION = 240.0  # the demo's 1.2 m per minute stands for 0.3 m per hour; a crew's walk is compressed alike


def crew_problem(goal: str):
    """FR1/FR2: the crew at staging, the request at ``goal``. Planned live by missions/crew_routing.py with the depth
    each road has when the crew would be on it."""
    from ...missions.crew_routing import CrewProblem, flood_alternatives, flood_route
    from ...decisions.tasks import ROUTE_MAX_WATER_M

    graph, flood = roads(), flood_model()

    def plan(t_s: float) -> dict[str, Any]:
        r = flood_route(graph, flood, t_s, "staging", goal, compression=TIME_COMPRESSION)
        alts = flood_alternatives(graph, flood, t_s, "staging", goal, k=3, compression=TIME_COMPRESSION)
        for a in alts:
            a["unsafe"] = (f"water {a['max_water_depth_m']:.2f} m deep when the crew would be there (limit "
                           f"{ROUTE_MAX_WATER_M} m)") if a["max_water_depth_m"] > ROUTE_MAX_WATER_M else None
            a["waypoints"] = [graph.nodes[n] for n in a["path"]]
        route = None
        if r is not None:
            route = {**r, "id": next((a["id"] for a in alts if a["path"] == r["path"]), "R*"), "target": goal,
                     "waypoints": [graph.nodes[n] for n in r["path"]], "signature": "->".join(r["path"])}
        return {"route": route, "alternatives": alts, "level_m": round(flood.level(t_s), 2),
                "why": None if route else "every road to the request is under more than 0.3 m of water by the time "
                                         "the crew would reach it"}
    return CrewProblem("flood", FloodLayers.label, plan, [{"id": "crew-1", "xy": STAGING, "at": "staging"}])


def _eval(stranded: list[str], at_s: float, goal: str):
    def run(res, sc) -> dict[str, Any]:
        from ...decisions.engines import rule_answers
        from ...decisions.tasks import d10_dispatch

        found = {a: actor_found(res, a) for a in stranded}
        f = flood_model()
        prob = crew_problem(goal)
        off = prob.plan(at_s)  # the offline evaluator: the same planner, from scratch
        chosen = off["route"]
        live = [h for h in (getattr(res, "crew_routes", None) or []) if h["depart_s"] <= at_s + 1e-6]
        last = live[-1] if live else None
        off_at_live = prob.plan(last["depart_s"])["route"] if last else None
        reqs = [{"id": f"Q{i + 1}", "priority": 1 if found[a]["found"] else 2} for i, a in enumerate(stranded)]
        teams = [{"id": "crew-1", "available": True, "eta_s": {q["id"]: 300 + 60 * i for i, q in enumerate(reqs)}},
                 {"id": "crew-2", "available": True, "eta_s": {q["id"]: 420 - 60 * i for i, q in enumerate(reqs)}}]
        from ...decisions.tasks import prepare
        dispatch = next(a.choice for a in rule_answers(prepare(d10_dispatch({"requests": reqs, "teams": teams})))
                        if a.name == "assignment")
        return {"label": FloodLayers.label, "water_level_m_at_end": round(f.level(res.duration_s), 2),
                "time_compression": TIME_COMPRESSION,
                "stranded": found,
                "stranded_found_fraction": round(sum(v["found"] for v in found.values()) / len(found), 3),
                "false_candidate_tracks": false_tracks(res),
                "route": {"options": [{k: o[k] for k in ("id", "length_m", "eta_s", "max_water_depth_m", "unsafe")}
                                      for o in off["alternatives"]],
                          "removed": [o["id"] for o in off["alternatives"] if o["unsafe"]],
                          "rules_pick": chosen["id"] if chosen else "hold", "exists": int(chosen is not None),
                          "max_water_depth_m": chosen["max_water_depth_m"] if chosen else None,
                          "path": chosen["signature"] if chosen else None,
                          "live_plans": len(live), "live_depart_s": last["depart_s"] if last else None,
                          "live_path": last["route"] if last else None,
                          "live_matches_offline": int(last is not None and off_at_live is not None
                                                      and last["route"] == off_at_live["signature"]),
                          "replanned": len({h["route"] for h in live})},
                "dispatch_rules_proposal": dispatch}
    return run


def scenarios():
    from ..scenarios import BLUE, KHAKI, MUSTARD, PINK, DroneInit, MetricLimit, Scenario, ScriptedAction, person, responder

    fm = flood_model()
    rd = roads()
    crew = responder((0.0, 78.0), [Wait(999)], heading=180.0)
    stranded = [person("stranded-1", "subject", (-28.0, -60.0), MUSTARD, [Wait(999)]),
                person("stranded-2", "bystander", (8.0, -66.0), PINK, [Wait(999)], pants=KHAKI),
                person("stranded-3", "bystander", (36.0, -60.0), BLUE, [Wait(999)])]
    out = []
    sc = Scenario(
        "FR1", "Flood: stranded people in rising water",
        "Water rises over the southern yards (screening model, time compressed). The pilot flies along the creek road "
        "at 28 m; three people wait in the water.",
        duration_s=40.0, drone=DroneInit(-45.0, -75.0, 28.0, 90.0, -70.0),
        actors=[crew] + stranded,
        limits=[MetricLimit("world.stranded_found_fraction", min=0.66), MetricLimit("world.false_candidate_tracks", max=2)],
        tags=["world:flood", "search"])
    sc.pilot_path = [(-45.0, -75.0, 28.0, 0.0), (48.0, -75.0, 28.0, 2.6)]
    sc.hazards = FloodLayers(fm, roads=rd, start="staging", goal="se-yards")
    sc.world_eval = _eval(["stranded-1", "stranded-2", "stranded-3"], 40.0, "se-yards")
    sc.crew_problem = lambda: crew_problem("se-yards")
    out.append(sc)

    sc = Scenario(
        "FR2", "Flood: route the crew around closed roads",
        "By the time the crew leaves, the creek road is under more than 0.3 m of water. Routes are computed by code; "
        "unsafe ones are removed before any decision engine sees them; a person approves the assignment.",
        duration_s=40.0, drone=DroneInit(0.0, -40.0, 34.0, 180.0, -60.0),
        actors=[crew, stranded[0]],
        limits=[MetricLimit("world.route.exists", min=1), MetricLimit("world.route.max_water_depth_m", max=0.3),
                MetricLimit("world.route.live_matches_offline", min=1)],
        tags=["world:flood", "routing", "decisions"])
    sc.hazards = FloodLayers(fm, roads=rd, start="staging", goal="+0,-30")
    sc.world_eval = _eval(["stranded-1"], 40.0, "+0,-30")
    sc.crew_problem = lambda: crew_problem("+0,-30")
    out.append(sc)

    # FL1: the static flood. The water is already there and does not move, so the run never depends on how fast the
    # drone finds anyone. Three people are stranded on dry mounds inside the flooded blocks (fixed positions, the same
    # every run). The drone launches from the pad by the staging area, sweeps the flooded blocks (the survey map fills
    # in as it goes), matches the reported clothing and puts a beacon on the person for the crew.
    reported = STRANDED["stranded-a"]
    sc = Scenario(
        "FL1", "Flood (static): survey the flooded blocks and beacon the stranded",
        "The south of the neighbourhood is under about 0.75 m of standing water; a few mounds stay dry. The drone "
        "launches from the pad, sweeps the flooded blocks, puts a beacon on the person in the yellow shirt and guides "
        "the crew along dry roads. The water does not rise: nothing here depends on how long the search takes.",
        duration_s=300.0, drone=DroneInit(FLOOD_PAD[0], FLOOD_PAD[1], 0.6, 180.0, -50.0),
        actors=[responder(STAGING, [Wait(999)], heading=180.0),
                person("stranded-a", "subject", reported, MUSTARD, [Wait(999)], vary=False),
                person("stranded-b", "bystander", STRANDED["stranded-b"], BLUE, [Wait(999)], vary=False),
                person("stranded-c", "bystander", STRANDED["stranded-c"], PINK, [Wait(999)], vary=False)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder")],
        limits=[MetricLimit("world.rescue_beacon_found", min=1), MetricLimit("world.rescue_unsafe_steps", max=0)],
        tags=["world:flood", "search", "rescue", "beacon", "static"])
    sc.hazards = FloodLayers(static_flood_model(), roads=rd, start="staging", goal=None)
    from .sar import SEARCH_CAMERA, SEARCH_PERCEPTION
    sc.detector = "ultralytics"  # the learned detector on a 1080p feed, a full-resolution YOLO pass
    sc.camera, sc.perception = SEARCH_CAMERA, SEARCH_PERCEPTION
    sc.rescue = dict(kind="flood", description="yellow shirt", last_seen_xy=(reported[0] - 22.0, reported[1] + 18.0),
                     radius_m=70.0, altitude_m=28.0, crew_speed_mps=3.0, max_depth_m=0.15)
    sc.world_eval = lambda res, scenario: {"rescue_beacon_found": int(bool((res.rescue or {}).get("beacon"))),
                                           "rescue_unsafe_steps": (res.rescue or {}).get("unsafe_steps", 0),
                                           "stranded": {k: actor_found(res, k) for k in STRANDED}}
    out.append(sc)

    for s in out:
        s.world = build_world
    return out
