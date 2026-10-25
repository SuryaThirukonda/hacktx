"""Dispatch (launch, search legs, description match, reports), operator-driven people, and the low-altitude
reflex from range sectors."""

import numpy as np

from overwatch.autonomy.controller import AutonomyController, AutonomyInputs, DispatchPlan
from overwatch.autonomy.reflex import decision_needed, nearest_obstacle_m, reflex, scene_sectors
from overwatch.autonomy.safety import DroneSafetyState
from overwatch.config import load_config
from overwatch.sim.actors import Actor, ActorSpec, Appearance, MoveTo, Wait
from overwatch.sim.world import BoxObj, TreeObj


class SimLink:
    is_sim = True

    def connected(self, now_ms):
        return True

    def send(self, cmd):
        pass


def inputs(now, alt, xy=(0.0, 0.0), heading=0.0):
    return AutonomyInputs(now_ms=now, subject_state="UNLOCKED", subject_box=None, image_size=(960, 540), hfov_deg=82.0,
                          vfov_deg=52.0, subject_xy=None, subject_vel=None, last_seen_xy=None, lost_s=None,
                          drone=DroneSafetyState(xy, alt, heading, 90.0, True), gimbal_pitch_deg=-60.0, calibration_valid=True)


def test_dispatch_takes_off_then_flies_to_the_area_and_the_legs():
    cfg = load_config().autonomy
    cfg.geofence = []
    a = AutonomyController(cfg, SimLink())
    plan = DispatchPlan("red shirt", (0.0, 0.0), [(0.0, 40.0), (30.0, 40.0), (30.0, 60.0)], altitude_m=20.0)
    a.start_dispatch(plan, 0.0, "test")
    st, _ = a.tick(inputs(50.0, 0.5))
    assert a.mode == "DISPATCH" and st.fsm == "TAKEOFF" and st.last_command.up_mps > 0
    assert st.last_command.forward_mps == 0.0  # no horizontal motion before it is airborne
    st, _ = a.tick(inputs(100.0, 19.0))
    assert st.fsm == "TRANSIT" and st.last_command.forward_mps > 1.0  # heading north towards (0, 40)
    assert st.route == [(0.0, 40.0), (30.0, 40.0), (30.0, 60.0)] and st.route_label == "to the search area"
    st, _ = a.tick(inputs(150.0, 20.0, xy=(0.0, 39.0)))  # at the first waypoint
    st, _ = a.tick(inputs(200.0, 20.0, xy=(0.0, 39.0)))
    assert st.fsm == "SEARCH" and st.route == [(30.0, 40.0), (30.0, 60.0)] and "leg" in (st.route_label or "")
    for t, xy in ((250.0, (30.0, 40.0)), (300.0, (30.0, 40.0)), (350.0, (30.0, 60.0)), (400.0, (30.0, 60.0))):  # fly the legs out
        st, _ = a.tick(inputs(t, 20.0, xy=xy))
    assert st.fsm == "COVERED" and st.route is None
    a.set_mode("FOLLOW", 500.0, "dispatch: matched")
    assert a.mode == "FOLLOW"
    a.resume_search(600.0, "operator rejected the match")
    assert a.mode == "DISPATCH" and plan.phase == "COVERED"


def test_operator_drives_a_person_and_the_script_waits():
    spec = ActorSpec("p", "bystander", (0.0, 0.0), 0.0, Appearance(), [MoveTo(0.0, 50.0, 1.4), Wait(99)])
    a = Actor(spec)
    for _ in range(30):
        a.update(0.1)
    y_scripted = a.pos[1]
    assert y_scripted > 1.0
    a.control("goto", x=20.0, y=a.pos[1], speed=3.0)
    for _ in range(120):
        a.update(0.1)
    assert abs(a.pos[0] - 20.0) < 0.5 and a.command["kind"] == "stop" and a.state_dict()["controlled"]
    a.control("drive", heading_deg=90.0, speed=1.4, hold_s=0.5)
    for _ in range(5):
        a.update(0.1)
    assert a.pos[0] > 20.12  # walking east while the key is held (accelerating from a stop)
    for _ in range(15):
        a.update(0.1)
    assert a.command["kind"] == "stop"  # the key was released: it lapsed
    a.control("resume")
    for _ in range(20):
        a.update(0.1)
    assert a.command is None and a.pos[1] > y_scripted  # back on the script towards (0, 50)


def test_appearance_varies_per_person_but_clothing_stays():
    red = Appearance(shirt=(40, 40, 210))
    a, b = red.varied("subject"), red.varied("decoy")
    assert a.shirt == b.shirt == (40, 40, 210)
    assert (a.skin, a.hair, a.sleeves, a.trousers) != (b.skin, b.hair, b.sleeves, b.trousers)
    assert red.varied("subject") == a  # deterministic


def test_scene_sectors_see_a_tree_at_canopy_height_and_the_reflex_brakes():
    trees = [TreeObj("t", (0.0, 10.0), canopy_r=2.5, canopy_z=5.0)]
    boxes = [BoxObj("house", "house", (-15.0, 10.0), (8.0, 8.0, 6.0))]
    sc = scene_sectors(boxes, trees, (0.0, 0.0, 5.5), 0.0)
    assert sc["sector_range_m"]["ahead"] < 8.5 and sc["path_ahead_m"] < 8.5  # the canopy is in the path
    assert sc["free_ahead_above_m"] == sc["max_range_m"]  # clear air above it
    assert sc["obstacle_top_m"] == 7.5 and decision_needed(sc)
    high = scene_sectors(boxes, trees, (0.0, 0.0, 12.0), 0.0)
    assert high["path_ahead_m"] == high["max_range_m"] and not decision_needed(high)  # above the canopy: nothing
    near = scene_sectors(boxes, trees, (0.0, 3.0, 5.5), 0.0)  # 4.5 m from the canopy: inside the brake range
    r = reflex(4.0, 0.0, 0.0, 0.0, near)
    assert r.active and r.forward < 2.0 and abs(r.right) > 0  # brake and steer towards the wider side
    close = scene_sectors(boxes, trees, (0.0, 6.5, 5.5), 0.0)
    r2 = reflex(4.0, 0.0, 0.0, 0.0, close)
    assert r2.forward < 0  # back off
    assert nearest_obstacle_m(boxes, trees, (0.0, 7.0, 5.5)) < 1.0
    assert nearest_obstacle_m(boxes, trees, (0.0, 0.0, 5.5)) > 7.0


def test_reflex_overrides_a_committed_climb_only_when_needed():
    cfg = load_config().autonomy
    cfg.geofence = []
    cfg.altitude_m, cfg.min_altitude_m = 7.0, 4.0
    a = AutonomyController(cfg, SimLink())
    scene = {"path_ahead_m": 20.0, "clearance_left_m": 20.0, "clearance_right_m": 20.0, "sectors_blocked": 0,
             "free_ahead_above_m": 25.0, "altitude_m": 7.0, "source": "test"}
    a.obstacle_sensor = lambda: scene
    a.set_mode("FOLLOW", 0.0)
    a.set_maneuver("climb", "decision:test", 0.0)
    lost = inputs(50.0, 7.0)
    lost.subject_state, lost.lost_s = "REACQUIRE", 1.0
    st, _ = a.tick(lost)
    assert st.maneuver == "climb" and st.last_command.up_mps > 0.3 and st.reflex is None
    scene["path_ahead_m"] = 2.0  # something right in front: the reflex wins
    st, _ = a.tick(inputs(100.0, 7.0))
    assert st.reflex and st.last_command.forward_mps < 0


def test_city_world_hides_the_street_behind_towers():
    """The city's towers are taller than the chase altitude's line of sight at the corners: a person just round a
    corner from the camera is hidden, which is the point of the world."""
    from overwatch.sim.worlds.city import ALT_M, build_world, street_graph

    w = build_world()
    assert w.launchpad and w.streets and len(w.streets["edges"]) == len(street_graph()["edges"])
    tall = [b for b in w.boxes if b.kind == "building" and b.size_lwh[2] >= 32.0]
    assert len(tall) >= 8 and ALT_M > max(b.size_lwh[2] for b in w.boxes if b.kind == "building") - 10
    x0, x1, y0, y1 = w.bounds
    assert all(x0 < b.center_xy[0] < x1 and y0 < b.center_xy[1] < y1 for b in w.boxes)
