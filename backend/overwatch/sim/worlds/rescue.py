"""Compact base-launch rescue scenarios, alongside the original large-world cases.

Requests describe a reported location. The mission must find rendered person pixels;
hidden actor roles are used only by evaluation, not by acquisition or beaconing.
"""
from functools import partial

import numpy as np

from ..actors import Wait
from ..hazards.grid import Grid
from ..hazards.layers import FireLayers, FloodLayers
from ..hazards.fire import FireModel
from ..hazards.water import FloodModel
from ..world import BoxObj, TreeObj, default_world, launchpad_objects

BOUNDS = (-20., 42., -16., 36.)
PAD = (0., 0.)


def build_world(kind):
    w = default_world()
    w.name = f"rescue-{kind}"
    w.bounds, w.texture_res_m = BOUNDS, .08
    w.launchpad = PAD
    w.geofence = [(-18, -14), (40, -14), (40, 34), (-18, 34)]
    w.boxes = launchpad_objects(PAD) + [BoxObj("rescue-crate", "crate", (13., 12.), (4., 4., 1.))]
    w.trees = [TreeObj(f"rescue-tree-{i}", point, canopy_r=2.5)
               for i, point in enumerate([(-10., 14.), (2., 27.), (30., 25.), (34., 6.)])]
    return w


BUILDERS = {kind: partial(build_world, kind) for kind in ("flood", "fire", "forest")}


def scenarios():
    from ..scenarios import BLUE, MUSTARD, DroneInit, MetricLimit, Scenario, ScriptedAction, person, responder
    result = []
    for sid, kind, title in (("FR3", "flood", "Flood refuge"), ("FF3", "fire", "Fire perimeter"),
                             ("SR5", "forest", "Forest clearing")):
        grid = Grid.over(BOUNDS, .5)
        layers = None
        if kind == "flood":
            x, y = grid.xy()
            ground = np.where((x >= 22) & (y >= 8), 0., 1.)
            layers = FloodLayers(FloodModel(grid, ground, .4, 0., .4))
        elif kind == "fire":
            fuel = np.ones((grid.ny, grid.nx))
            layers = FireLayers(FireModel(grid, fuel, [(-16., -10., 0.)], base_ros_mps=.03),
                                keep_out_margin_s=15.)
        sc = Scenario(sid, f"{title}: launch, locate, review and beacon",
                      "SIMULATED: launch from base and search a reported location for a yellow shirt. "
                      "A measured beacon requires human confirmation before crew movement. "
                      "Keep visual contact, guide the crew, then return to base.", duration_s=110,
                      drone=DroneInit(*PAD, .6, 45., -50.), world=BUILDERS[kind], hazards=layers,
                      actors=[responder((5., 8.), [Wait(999)], heading=45),
                              person("reported-person", "subject", (20., 14.), MUSTARD, [Wait(999)], vary=False),
                              person("other-person", "bystander", (7., 23.), BLUE, [Wait(999)], vary=False)],
                      actions=[ScriptedAction(1., "lock_responder", "responder"),
                               ScriptedAction(1., "dispatch", dict(description="yellow shirt", last_seen_xy=(20., 14.),
                                                                    radius_m=12., altitude_m=24.))],
                      tags=[f"world:{'sar' if kind == 'forest' else kind}", "search", "rescue", "beacon"],
                      limits=[MetricLimit("world.rescue_beacon_found", min=1), MetricLimit("world.rescue_unsafe_steps", max=0)],
                      world_eval=lambda res, scenario: {"rescue_beacon_found": int(bool((res.rescue or {}).get("beacon"))),
                                                       "rescue_unsafe_steps": (res.rescue or {}).get("unsafe_steps", 0)},
                      rescue=dict(kind=kind, description="yellow shirt", last_seen_xy=(20., 14.),
                                  radius_m=12., altitude_m=24., crew_speed_mps=3., max_depth_m=.15))
        result.append(sc)
    return result
