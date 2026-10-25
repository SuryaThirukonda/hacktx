"""Firefighter routing world (Structure and routing): a building with doors, windows, a garage and stairs on sides A
to D; a grass fire spreading towards it (arrival times by shortest path, ember-style), a smoke plume, keep-out zones,
crews, and an occupant outside.

Scale: 200 by 200 m, the plan's size (the original 120 m core holds the house, the road and the staging area). Fire spread and smoke are screening models with time compressed for the demo, labelled
as such everywhere. Nothing here says an entry is safe or a structure stable.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..actors import Wait
from ..hazards.fire import FireModel
from ..hazards.grid import Grid
from ..hazards.layers import FireLayers
from ..world import BoxObj, TreeObj, World, ZoneSpec, launchpad_objects
from .scoring import actor_found
from .spec import BuildingSpec, OpeningSpec

BOUNDS = (-100.0, 100.0, -100.0, 100.0)
TIME_COMPRESSION = 8.0  # the fire runs 8x faster than the spread rate would give, so a scenario fits in a minute
KEEP_OUT_MARGIN_S = 120.0 / TIME_COMPRESSION  # "reached within two minutes" in compressed time
STAGING = (40.0, 45.0)
CREW_LEAVES_S = 20.0  # FF1: when the crew sets off (planning later, every door is reached by the fire in time)
BUILDING = BuildingSpec(
    id="bldg-f", name="Two-storey house", center=(0.0, 0.0), size_ew_m=12.0, size_ns_m=9.0, height_m=6.5, side_a="S",
    color=(140, 142, 146),
    openings=[OpeningSpec(id="ff-door-a", kind="door", side="A", offset_m=-2.0, width_m=1.0, height_m=2.1),
              OpeningSpec(id="ff-window-a", kind="window", side="A", offset_m=2.5, width_m=1.4, height_m=1.2, sill_m=1.0),
              OpeningSpec(id="ff-window-b", kind="window", side="B", offset_m=0.0, width_m=1.2, height_m=1.2, sill_m=3.6),
              OpeningSpec(id="ff-door-c", kind="door", side="C", offset_m=1.5, width_m=1.0, height_m=2.1),
              OpeningSpec(id="ff-garage-d", kind="garage_door", side="D", offset_m=0.0, width_m=2.8, height_m=2.3),
              OpeningSpec(id="ff-stairs-c", kind="stairs", side="C", offset_m=-3.5, width_m=1.2, height_m=2.4)])


def fire_model() -> FireModel:
    g = Grid.over(BOUNDS, 1.0)
    X, Y = g.xy()
    fuel = np.ones((g.ny, g.nx), bool)
    fuel[(np.abs(X - BUILDING.center[0]) < BUILDING.size_ew_m / 2 + 2) &
         (np.abs(Y - BUILDING.center[1]) < BUILDING.size_ns_m / 2 + 2)] = False  # the house and its gravel apron
    fuel[np.abs(Y - 30.0) < 3.5] = False  # the access road is a firebreak
    # time compressed for the demo: spread is fast so the scenario fits in a minute
    return FireModel(g, fuel, [(-45.0, -45.0, 0.0)], base_ros_mps=0.45, wind_speed_mps=2.5, wind_to_deg=45.0)


def build_world() -> World:
    w = World(bounds=BOUNDS, texture_res_m=0.05, seed=51)
    w.name = "fire"
    w.painter = _paint
    w.boxes = [BUILDING.to_box(),
               BoxObj("engine-1", "vehicle", (38.0, 30.0), (8.5, 2.5, 3.0), heading_deg=90.0, color=(96, 98, 104),
                      reflective=True)]
    w.trees = [TreeObj(f"ff-tree-{k}", (float(x), float(y)), canopy_r=3.0, color=(40, 90, 50))
               for k, (x, y) in enumerate([(-30, 20), (-20, -30), (25, -25), (30, 10), (-40, -10)])]
    rng = np.random.default_rng(52)  # scrub trees and two outbuildings beyond the original core
    while len(w.trees) < 70:
        x, y = rng.uniform(-96, 96, 2)
        if abs(x) < 62 and abs(y) < 62 or abs(y - 30.0) < 6:
            continue
        w.trees.append(TreeObj(f"ff-tree-{len(w.trees)}", (float(x), float(y)), canopy_r=float(rng.uniform(2.2, 3.6)),
                               color=(40, int(rng.integers(84, 98)), 50)))
    w.boxes += [BoxObj("shed-1", "shed", (-80.0, 70.0), (6.0, 4.0, 3.0), color=(120, 124, 128)),
                BoxObj("barn-1", "barn", (75.0, -75.0), (14.0, 9.0, 5.5), color=(110, 112, 118))]
    w.zones = [ZoneSpec("ff-staging", "Staging", [(32, 38), (48, 38), (48, 52), (32, 52)], kind="staging")]
    w.geofence = [(-96, -96), (96, -96), (96, 96), (-96, 96)]
    return w


def _paint(tex, img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    import cv2

    res = tex.res_m
    h, w = img.shape[:2]
    dry = np.array([70, 128, 140], np.float32)  # dry grass, low saturation
    img = img * 0.35 + dry[None, None, :] * 0.65
    m = np.zeros((h, w), np.uint8)
    a, b = tex.to_px(BOUNDS[0], 30.0), tex.to_px(BOUNDS[1], 30.0)
    cv2.line(m, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), 255, max(1, int(7.0 / res)), cv2.LINE_AA)
    p, q = tex.to_px(-8.0, -6.5), tex.to_px(8.0, 6.5)
    cv2.rectangle(m, (int(p[0]), int(q[1])), (int(q[0]), int(p[1])), 255, -1)
    f = (m.astype(np.float32) / 255.0)[..., None]
    return img * (1 - f) + np.array([150, 152, 154], np.float32)[None, None, :] * f


# --- the static wildfire (WF1): burning patches on the search-and-rescue forest that do not spread ------------------
# The map is the 1.5 km woodland of the search-and-rescue world (its trail, creek, clearings and 15,900 trees); the
# fire is three burning patches in the core and a ring of fire round one of its clearings, with the person inside.
BURNS = [(-90.0, -20.0, 40.0), (40.0, 60.0, 34.0), (85.0, -40.0, 30.0)]  # (x, y, radius): areas on fire
RING = (10.0, -55.0, 30.0, 13.0)  # (x, y, outer, inner): the clearing at (10, -55) encircled; the person inside
RING_GAP_DEG = (205.0, 36.0)  # (bearing, width): an unburnt break in the ring on the trailhead side, the crew's way in
WILD_PEOPLE = {"stranded": (10.0, -55.0), "hiker": (-55.0, -45.0)}  # fixed positions, the same every run
FIRE_GRID_RES_M = 4.0


def _wild_masks(g: Grid) -> np.ndarray:
    X, Y = g.xy()
    burning = np.zeros((g.ny, g.nx), bool)
    for cx, cy, r in BURNS:
        burning |= (X - cx) ** 2 + (Y - cy) ** 2 <= r * r
    cx, cy, ro, ri = RING
    d2 = (X - cx) ** 2 + (Y - cy) ** 2
    ring = (d2 <= ro * ro) & (d2 >= ri * ri)
    bearing = np.degrees(np.arctan2(X - cx, Y - cy)) % 360.0  # compass bearing from the clearing's centre
    off = (bearing - RING_GAP_DEG[0] + 180.0) % 360.0 - 180.0
    ring &= np.abs(off) > RING_GAP_DEG[1] / 2  # the break: the A* access route threads through it
    burning |= ring
    return burning


def static_fire_model() -> FireModel:
    """Fuel only where the fire already is, lit everywhere at once with a very fast spread inside: the burning area is
    the patches themselves from the first second and never grows. Smoke drifts from them; keep-out is the patches."""
    from .sar import BOUNDS as SAR_BOUNDS

    g = Grid.over(SAR_BOUNDS, FIRE_GRID_RES_M)
    burning = _wild_masks(g)
    ignitions = [(float(x), float(y), 0.0) for x, y, _ in BURNS] + [(RING[0] + (RING[2] + RING[3]) / 2, RING[1], 0.0)]
    return FireModel(g, burning.astype(float), ignitions, base_ros_mps=5000.0, wind_speed_mps=2.0, wind_to_deg=45.0)


def wildfire_world() -> World:
    """The search-and-rescue forest with the trees inside the burn charred and shrunk."""
    from .sar import build_world as sar_world

    w = sar_world()
    w.name = "wildfire"
    g = Grid.over(w.bounds, FIRE_GRID_RES_M)
    burning = _wild_masks(g)
    for t in w.trees:
        r, c = g.cell(float(t.xy[0]), float(t.xy[1]))
        if 0 <= r < g.ny and 0 <= c < g.nx and burning[r, c]:
            t.color = (36, 44, 48)
            t.canopy_r = max(1.2, t.canopy_r * 0.6)
    return w


def static_blocked(fire: FireModel) -> np.ndarray:
    g = fire.grid
    X, Y = g.xy()
    return (np.abs(X - BUILDING.center[0]) < BUILDING.size_ew_m / 2) & (np.abs(Y - BUILDING.center[1]) < BUILDING.size_ns_m / 2)


def crew_route(fire, t_s, start, goal, margin):
    """Compatibility adapter: 3D previews use the same time-aware planner as the live crew service."""
    from ...missions.crew_routing import fire_routes
    return fire_routes(fire, t_s, start, {"goal": goal}, static_blocked(fire), margin,
                       compression=TIME_COMPRESSION, smoke=fire.smoke(t_s))["goal"]


def crew_problem():
    """FF1/FF2: the crew at staging, the request: reach a door. Planned live by missions/crew_routing.py: a cell is
    usable only if the crew would be there two minutes (compressed) before the fire; smoke costs extra."""
    from ...missions.crew_routing import CrewProblem, fire_routes

    f = fire_model()
    blocked = static_blocked(f)
    doors = [op for op in BUILDING.openings if op.kind == "door"]
    goals = {}
    for op in doors:
        ox, oy = BUILDING.opening_xy(op)
        (_, _), (nx, ny), _ = BUILDING.face(op.side)
        goals[op.id] = (ox + nx * 2.5, oy + ny * 2.5)
    side = {op.id: op.side for op in doors}

    def plan(t_s: float) -> dict[str, Any]:
        smoke = f.smoke(t_s)
        res = fire_routes(f, t_s, STAGING, goals, blocked, KEEP_OUT_MARGIN_S, compression=TIME_COMPRESSION, smoke=smoke)
        alts = [{**r, "id": k, "label": f"door {side[k]}", "target": k, "side": side[k], "max_water_depth_m": 0.0,
                 "hazards_crossed": r["keep_out_cells"], "unsafe": None} for k, r in res.items() if r is not None]
        route = min(alts, key=lambda a: a["eta_s"]) if alts else None
        if route is not None:
            route = {**route, "signature": f"{route['target']}:{route['cells']}:{route['length_m']}"}
        return {"route": route, "alternatives": alts,
                "why": None if route else "every door is reached by the fire within the margin of the crew getting there"}
    return CrewProblem("fire", FireLayers.label, plan, [{"id": "crew-1", "xy": STAGING, "at": "staging"}])


def _eval(at_s: float, occupant: str | None):
    def run(res, sc) -> dict[str, Any]:
        from ...decisions.engines import rule_answers
        from ...decisions.tasks import d11_opening, prepare

        f = fire_model()
        smoke = f.smoke(at_s)
        keep = f.keep_out(at_s, KEEP_OUT_MARGIN_S)
        openings = []
        for op in BUILDING.openings:
            x, y = BUILDING.opening_xy(op)
            (_, _), (nx, ny), _ = BUILDING.face(op.side)
            r, c = f.grid.cell(x + nx * 2.0, y + ny * 2.0)
            facts = {"in_view": True, "smoke_layer": bool(smoke[r, c] > 0.25), "blocked_marked": False,
                     "obstructed_fraction": 0.0}
            cond = next(a.choice for a in rule_answers(prepare(d11_opening(facts))) if a.name == "condition")
            openings.append({"id": op.id, "side": op.side, "kind": op.kind, "smoke": round(float(smoke[r, c]), 2),
                             "in_keep_out_2min": bool(keep[r, c]), "d11_rules": cond})
        prob = crew_problem()
        off = prob.plan(at_s)  # the offline evaluator: the same planner, from scratch
        route = off["route"]
        live = [h for h in (getattr(res, "crew_routes", None) or []) if h["depart_s"] <= at_s + 1e-6]
        last = live[-1] if live else None
        off_at_live = prob.plan(last["depart_s"])["route"] if last else None
        out = {"label": FireLayers.label, "time_compression": TIME_COMPRESSION, "openings": openings,
               "crew_route_target": route["target"] if route else None,
               "crew_route_found": int(route is not None),
               "crew_route_keep_out_cells": route["keep_out_cells"] if route else None,
               "crew_route_length_m": route["length_m"] if route else None,
               "crew_route_smoke_m": route["smoke_m"] if route else None,
               "crew_route_live_plans": len(live),
               "crew_route_live_matches_offline": int(last is not None and off_at_live is not None
                                                      and last["route"] == off_at_live["signature"]),
               "unclear_openings": sum(1 for o in openings if o["d11_rules"] == "unclear"),
               "declared_safe": 0}  # no software output can say "safe": D11 has no such option
        if occupant:
            out["occupant"] = actor_found(res, occupant)
        return out
    return run


def scenarios():
    from ..scenarios import MUSTARD, DroneInit, MetricLimit, Scenario, person, responder

    hz = FireLayers(fire_model(), keep_out_margin_s=KEEP_OUT_MARGIN_S, building=BUILDING, staging=STAGING,
                    route_fn=crew_route)
    crews = [responder(STAGING, [Wait(999)], heading=225.0)]
    out = []
    sc = Scenario(
        "FF1", "Fire: route a crew to a door outside the keep-out zone",
        "A grass fire spreads from the south-west towards the house (screening model, time compressed). Openings in "
        "the keep-out area (reached within two minutes, scaled by the time compression) are flagged; the crew's walking route to the nearest door outside it is "
        "computed by code and avoids the keep-out cells.",
        duration_s=28.0, drone=DroneInit(18.0, -22.0, 32.0, 330.0, -55.0), actors=crews,
        limits=[MetricLimit("world.crew_route_found", min=1), MetricLimit("world.crew_route_keep_out_cells", max=0),
                MetricLimit("world.declared_safe", max=0), MetricLimit("world.crew_route_live_matches_offline", min=1)],
        tags=["world:fire", "structure", "routing"])
    sc.hazards = hz
    sc.world_eval = _eval(CREW_LEAVES_S, None)
    sc.crew_problem = crew_problem
    sc.scene = None
    out.append(sc)

    sc = Scenario(
        "FF2", "Fire: smoke over the building and an occupant outside",
        "Smoke drifts north-east over sides B and C; an occupant waits outside near side C. Openings under smoke read "
        "'unclear' (D11 rules); the occupant is detected when the smoke thins.",
        duration_s=40.0, drone=DroneInit(-14.0, 22.0, 30.0, 150.0, -58.0),
        actors=crews + [person("occupant", "subject", (4.0, 8.5), MUSTARD, [Wait(999)])],
        limits=[MetricLimit("world.unclear_openings", min=1), MetricLimit("world.declared_safe", max=0),
                MetricLimit("world.occupant.found", min=1)],
        tags=["world:fire", "structure", "smoke"])
    sc.hazards = hz
    sc.world_eval = _eval(40.0, "occupant")
    sc.crew_problem = crew_problem
    out.append(sc)

    for s in out:
        s.world = build_world

    # WF1: the static wildfire. Burning patches in a forest that do not spread, one of them a ring of fire round a
    # clearing with a person stranded inside (fixed positions, the same every run). The drone launches from the fire
    # camp, sweeps the burn area while the survey map fills in, puts a beacon on the person and guides the crew.
    from ..scenarios import BLUE, ScriptedAction
    from .sar import LAUNCHPAD as WILD_PAD
    reported = WILD_PEOPLE["stranded"]
    sc = Scenario(
        "WF1", "Wildfire (static): survey the burn and beacon the person in the clearing",
        "On the search-and-rescue woodland: three patches of forest are burning and a ring of fire surrounds a "
        "clearing with someone inside, open only through a break on the trailhead side. Nothing spreads: the fire is "
        "where it is for the whole run. The drone launches from the trailhead pad, sweeps the burn area, matches the "
        "yellow shirt and puts a beacon on the person; the crew's way in (A* round the fire, through the break) is "
        "assessed the moment the beacon is placed and drawn in 3D.",
        duration_s=300.0, drone=DroneInit(WILD_PAD[0], WILD_PAD[1], 0.6, 45.0, -50.0),
        actors=[responder((WILD_PAD[0] + 10.0, WILD_PAD[1] + 2.0), [Wait(999)], heading=45.0),
                person("stranded", "subject", reported, MUSTARD, [Wait(999)], vary=False),
                person("hiker", "bystander", WILD_PEOPLE["hiker"], BLUE, [Wait(999)], vary=False)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder")],
        limits=[MetricLimit("world.rescue_beacon_found", min=1), MetricLimit("world.rescue_unsafe_steps", max=0)],
        tags=["world:fire", "search", "rescue", "beacon", "static"])
    sc.hazards = FireLayers(static_fire_model(), keep_out_margin_s=60.0, staging=WILD_PAD)
    from .sar import SEARCH_CAMERA, SEARCH_PERCEPTION
    sc.detector = "ultralytics"  # the learned detector on the forest frames (the blob stand-in struggles with the burn)
    sc.camera, sc.perception = SEARCH_CAMERA, SEARCH_PERCEPTION  # a 1080p feed, a full-resolution YOLO pass
    sc.rescue = dict(kind="fire", description="yellow shirt", last_seen_xy=(reported[0] + 30.0, reported[1] + 25.0),
                     radius_m=80.0, altitude_m=30.0, crew_speed_mps=2.0)  # 30 m up: a person is ~70 px tall at 1080p
    sc.world_eval = lambda res, scenario: {"rescue_beacon_found": int(bool((res.rescue or {}).get("beacon"))),
                                           "rescue_unsafe_steps": (res.rescue or {}).get("unsafe_steps", 0),
                                           "stranded": actor_found(res, "stranded")}
    sc.world = wildfire_world
    out.append(sc)
    return out
