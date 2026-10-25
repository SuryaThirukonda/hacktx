"""Integration boundaries: coverage is visibility evidence, rescue never bypasses review/control."""
import base64
from types import SimpleNamespace

import numpy as np
import pytest

from overwatch.config import Config
from overwatch.evaluation.scenario_runner import HeadlessRunner
from overwatch.sim.survey import CameraSurvey
from overwatch.sim.visibility import Occluders
from overwatch.sim.world import BoxObj, World
from overwatch.missions.sar_guidance import access_route
from overwatch.sim.hazards.grid import Grid
from overwatch.sim.hazards.water import FloodModel


def test_roof_blocks_ground_even_inside_camera_footprint():
    world = World(bounds=(-5, 5, -5, 5), boxes=[BoxObj("roof", "carport", (0, 0), (4, 4, .3), z0=3)])
    visible = Occluders(world).visible(np.array([0, 0, 10]), np.array([[0, 0], [4, 0]]))
    assert visible.tolist() == [False, True]


def test_survey_never_marks_hidden_ground_and_retains_last_seen_age():
    world = World(bounds=(-4, 4, -4, 4), boxes=[BoxObj("roof", "carport", (0, 0), (6, 6, .3), z0=3)])
    survey = CameraSurvey(world)
    camera = SimpleNamespace(pose=SimpleNamespace(position=np.array([0, 0, 10])),
                             intr=SimpleNamespace(width=100, height=100),
                             project=lambda pts: (np.full((len(pts), 2), 50), np.ones(len(pts))))
    survey.update(0, camera)
    image = np.frombuffer(base64.b64decode(survey.encode(0)["data"]), dtype=np.uint8)
    assert (image == 0).any() and survey.encode(0)["seen_m2"] < 64
    first = survey.last.copy()
    survey.encode(5)
    assert np.array_equal(first, survey.last)  # displaying age does not observe new cells


@pytest.mark.parametrize("sid", ["FR3", "FF3", "SR5"])
def test_beacon_requires_review_and_stops_on_close_or_pilot_override(sid):
    runner = HeadlessRunner(Config(), sid, engine="rules")
    p, mission = runner.pipeline, runner.pipeline.rescue
    mission.phase = "REVIEW"
    mission.beacon = {"xy": (20, 14), "track_id": "test", "status": "pending"}
    start = runner.sim.actor("responder").pos.copy()
    mission.route = [start + [1, 0]]
    mission.step_sim(.1)
    assert np.array_equal(start, runner.sim.actor("responder").pos)
    mission.confirm("test human")
    assert mission.confirmed and mission.beacon["status"] == "confirmed"
    p.autonomy.mode = "MANUAL"
    mission.tick(2000)
    assert mission.phase == "PAUSED"
    mission.step_sim(.1)
    assert np.array_equal(start, runner.sim.actor("responder").pos)
    p.stop_incident()
    assert not mission.active() and mission.beacon is None


def test_rejected_beacon_cannot_dispatch():
    runner = HeadlessRunner(Config(), "SR5", engine="rules")
    mission = runner.pipeline.rescue
    mission.phase = "REVIEW"
    mission.beacon = {"xy": (20, 14), "track_id": "wrong", "status": "pending"}
    mission.reject("operator")
    assert mission.phase == "REJECTED" and not mission.confirmed and mission.route == []
    with pytest.raises(ValueError):
        mission.confirm()


def test_live_context_cannot_activate_simulated_rescue():
    runner = HeadlessRunner(Config(), "FR3", engine="rules")
    runner.pipeline.mode = "LIVE"
    assert not runner.pipeline.rescue.active()
    runner.pipeline.rescue.tick(2000)
    assert runner.pipeline.rescue.phase == "IDLE" and runner.pipeline.autonomy.mode == "MANUAL"


def test_route_checks_rising_water_at_arrival_not_only_departure():
    grid = Grid.over((0, 10, 0, 6), 1)
    flood = FloodModel(grid, np.zeros((grid.ny, grid.nx)), 0, 1, 1)
    current = access_route((1, 2), (8, 2), flood=flood, max_depth_m=.15)
    future = access_route((1, 2), (8, 2), flood=flood, max_depth_m=.15, walk_mps=.1)
    assert current["status"] == "access_route" and future["status"] == "blocked"
