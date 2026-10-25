import math

import numpy as np

from overwatch.config import SARCfg
from overwatch.missions.sar import triage_score
from overwatch.missions.sar_guidance import access_route
from overwatch.sim.hazards.grid import Grid
from overwatch.sim.hazards.water import FloodModel


def flood(barrier=True, gap=False):
    ground = np.ones((12, 12))
    if barrier:
        ground[:, 5] = 0
        if gap:
            ground[9:, 5] = 1
    return FloodModel(Grid(0, 0, 1, 12, 12), ground, .4, 0, .4)


def test_interpretable_score_hazard_and_stationary_normal():
    cfg = SARCfg()
    assert triage_score({"flood_depth_m": .5}, 0, cfg)["flag"]
    assert triage_score({"fire_keep_out": True}, 0, cfg)["flag"]
    normal = triage_score({"flood_depth_m": 0}, 100, cfg)
    assert not normal["flag"] and normal["value"] == 1
    assert [r["code"] for r in normal["reasons"]] == ["stationary"]


def test_flood_barrier_without_route_is_blocked():
    route = access_route((1.5, 3.5), (9.5, 3.5), flood=flood())
    assert route["status"] == "blocked" and route["points"] == []


def test_route_detours_around_flood_and_every_edge_is_assessed():
    model = flood(gap=True)
    route = access_route((1.5, 3.5), (9.5, 3.5), flood=model)
    assert route["status"] == "access_route" and route["length_m"] > 8
    for a, b in zip(route["points"], route["points"][1:]):
        for t in np.linspace(0, 1, 8):
            assert model.depth(a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1]), 0) <= .15


def test_route_never_walks_to_a_person_in_water():
    route = access_route((1.5, 3.5), (5.5, 3.5), flood=flood())
    assert route["status"] == "approach_only"
    assert route["target_accessible"] is False and route["max_water_depth_m"] == 0
    assert math.dist(route["points"][-1], (5.5, 3.5)) > 0


def test_no_navigation_map_or_unsafe_start_does_not_invent_route():
    assert access_route((1, 1), (3, 3))["status"] == "unavailable"
    assert access_route((5.5, 3.5), (9.5, 3.5), flood=flood())["status"] == "blocked"
