"""Phase 14: hazard screening models, search probability, coverage and simulated thermal."""

import math

import numpy as np

from overwatch.autonomy.coverage import footprint_width_m, lawnmower, path_length
from overwatch.geometry import probability as prob
from overwatch.sim.hazards.fire import FireModel
from overwatch.sim.hazards.grid import Grid
from overwatch.sim.hazards.water import FloodModel, RoadGraph, candidate_routes, shortest
from overwatch.sim.sensors.thermal import render


def flood_fixture():
    g = Grid.over((0, 100, 0, 100), 1.0)
    X, Y = g.xy()
    ground = 0.02 * X  # slopes up to the east: the west floods first
    return g, FloodModel(g, ground, level0_m=0.0, rise_m_per_min=0.6, level_max_m=1.2)


def test_flood_rises_and_closes_low_roads():
    g, f = flood_fixture()
    assert f.level(0) == 0.0 and f.level(60) == 0.6 and f.level(600) == 1.2
    assert f.depth(10, 50, 60) > f.depth(25, 50, 60) >= 0.0
    assert f.depth(90, 50, 60) == 0.0
    # A low road dips west through M; the detour runs east along higher ground
    roads = RoadGraph({"A": (25, 10), "M": (5, 50), "B": (25, 90), "C": (60, 10), "D": (60, 90)},
                      [("A", "M"), ("M", "B"), ("A", "C"), ("C", "D"), ("D", "B")])
    assert shortest(roads, f, 0, "A", "B")["path"] == ["A", "M", "B"]  # dry: the short low road
    r = shortest(roads, f, 75, "A", "B")  # level 0.75 m: about 0.65 m at M, closed above 0.3 m
    assert r["path"] == ["A", "C", "D", "B"] and r["max_water_depth_m"] <= 0.3
    opts = candidate_routes(roads, f, 75, "A", "B", k=2)
    assert {o["id"] for o in opts} == {"R1", "R2"}
    assert any(o["max_water_depth_m"] > 0.3 for o in opts)  # the closed road is still offered, flagged
    assert f.flooded_polygons(60)


def test_fire_arrival_wind_and_firebreak():
    g = Grid.over((0, 100, 0, 100), 1.0)
    fuel = np.ones((g.ny, g.nx), bool)
    fuel[:, 70:73] = False  # a road as a firebreak
    m = FireModel(g, fuel, [(30.0, 50.0, 0.0)], base_ros_mps=0.1, wind_speed_mps=3.0, wind_to_deg=90.0)
    t = m.arrival()
    down = t[g.cell(50, 50)]
    up = t[g.cell(10, 50)]
    assert down < up  # spreads faster downwind (east)
    assert np.isinf(t[g.cell(90, 50)])  # the firebreak stops it
    assert m.burning(down + 1)[g.cell(50, 50)] and not m.burning(down - 1)[g.cell(50, 50)]
    assert m.keep_out(down - 60, margin_s=120)[g.cell(50, 50)]
    s = m.smoke(down)
    assert s[g.cell(65, 50)] > s[g.cell(10, 50)]  # smoke drifts downwind
    assert m.burning_polygons(down)


def test_probability_prior_update_and_pod():
    g = Grid.over((-200, 200, -200, 200), 5.0)
    X, Y = g.xy()
    p = prob.prior(X, Y, (0, 0), "walk_away")
    assert math.isclose(p.sum(), 1.0, rel_tol=1e-9)
    d = np.hypot(X, Y)
    ring = p[(d > 60) & (d < 120)].sum()
    assert ring > p[d > 180].sum()
    assert math.isclose(prob.pod(20.0, 20.0), 1 - math.exp(-1))
    searched = (X > 0)
    q = prob.update(p, searched, 0.8)
    assert q[searched].sum() < p[searched].sum() and math.isclose(q.sum(), 1.0)
    poa = prob.sector_poa(q, {"east": X > 0, "west": X <= 0})
    assert poa["west"] > poa["east"]


def test_coverage_lawnmower_spacing_and_extent():
    w = footprint_width_m(40.0, 82.0)
    assert 65 < w < 75  # about 70 m across at 40 m with an 82 degree lens, straight down
    wps = lawnmower([(0, 0), (100, 0), (100, 60), (0, 60)], 20.0, heading_deg=90.0)
    ys = sorted({round(p[1]) for p in wps})
    assert ys == [10, 30, 50]  # passes 20 m apart, inside the sector
    assert all(0 <= p[0] <= 100 for p in wps)
    assert 290 < path_length(wps) < 350


def test_simulated_thermal_is_labelled():
    img = render((320, 240), [(100, 100, 110, 130)])
    assert img.shape == (240, 320, 3)
    assert img[115, 105].mean() > img[200, 300].mean()  # the person is warmer than the background


def test_flood_and_fire_3d_payloads():
    from overwatch.sim.scenarios import all_scenarios

    sc = all_scenarios()
    # Flood 3D payload (FR2)
    fr2 = sc["FR2"].hazards
    w_flood = fr2.world_3d()
    assert w_flood["kind"] == "flood" and "screening" in w_flood["label"]
    assert len(w_flood["ground_m"]) == w_flood["grid"]["nx"] * w_flood["grid"]["ny"]
    f_flood0 = fr2.frame_3d(0.0)
    f_flood40 = fr2.frame_3d(40.0)
    assert f_flood40["level_m"] > f_flood0["level_m"]
    assert f_flood40["flooded_area_m2"] > f_flood0["flooded_area_m2"]
    assert f_flood40["closed_roads"]  # low creek road is closed by 40 s
    assert f_flood40["crew_route"] is not None and f_flood40["crew_route"]["max_water_depth_m"] <= 0.3

    # Fire 3D payload (FF1 and FF2)
    ff1 = sc["FF1"].hazards
    w_fire = ff1.world_3d()
    assert w_fire["kind"] == "fire" and "screening" in w_fire["label"]
    assert len(w_fire["arrival_s"]) == w_fire["grid"]["nx"] * w_fire["grid"]["ny"]
    f_fire = ff1.frame_3d(28.0)
    assert f_fire["burning_area_m2"] > 0 and f_fire["keep_out_area_m2"] >= f_fire["burning_area_m2"]
    assert f_fire["smoke_source_xy"] is not None and f_fire["smoke_strength"] > 0
    assert len(f_fire["openings"]) == 6
    assert f_fire["crew_route"] is not None and f_fire["crew_route"]["keep_out_cells"] == 0
