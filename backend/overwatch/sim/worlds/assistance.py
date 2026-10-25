"""Small deterministic assistance demo, independent of pursuit/test12.

Actors have no distress flags. Triage must observe pixels and query the labelled
flood map at calibrated detections. Actor names below are evaluation fixtures only.
"""
import numpy as np

from ..hazards.grid import Grid
from ..hazards.layers import FloodLayers
from ..hazards.water import FloodModel
from ..world import BoxObj, default_world


def build_world():
    world = default_world()
    world.name = "assistance-training"
    world.bounds = (-20.0, 40.0, -15.0, 35.0)
    world.texture_res_m = .05
    world.trees = []
    world.boxes = [BoxObj("access-obstacle", "crate", (13.0, 12.0), (4.0, 4.0, 1.0))]
    world.geofence = [(-18, -13), (38, -13), (38, 33), (-18, 33)]
    return world


def flood_map():
    grid = Grid.over((-20, 40, -15, 35), .5)
    x, y = grid.xy()
    ground = np.where((x >= 19) & (y >= 8), 0.0, 1.0)
    return FloodModel(grid, ground, .4, 0.0, .4)


def scenario():
    from ..actors import Wait
    from ..scenarios import BLUE, RED, DroneInit, Scenario, ScriptedAction, person, responder
    return Scenario(
        "SAR1", "SAR: detect, review and guide to an assistance location",
        "SIMULATED: one observed person overlaps flood water, another is on dry ground. "
        "Confirm the assistance card to send map-based guidance and acknowledge on /responder. "
        "The marked route stops at a dry approach point; it never enters the water.",
        duration_s=35, drone=DroneInit(13, 9, 24, 0, -78),
        actors=[responder((5, 10), [Wait(999)], heading=90),
                person("assistance-fixture", "subject", (20, 14), RED, [Wait(999)], vary=False),
                person("dry-fixture", "bystander", (7, 18), BLUE, [Wait(999)], vary=False)],
        actions=[ScriptedAction(1.0, "lock_responder", "responder")],
        world=build_world, hazards=FloodLayers(flood_map()), tags=["world:flood", "search", "sar", "review-demo"])
