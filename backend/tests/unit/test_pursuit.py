"""Autonomous pursuit: street-map routes from observed motion, D12 rules, the hold on unverified re-bindings."""

import numpy as np

from overwatch.autonomy.controller import AutonomyController, AutonomyInputs
from overwatch.autonomy.pursuit import describes, route_options, snap
from overwatch.autonomy.safety import DroneSafetyState
from overwatch.config import load_config
from overwatch.decisions.engines import rule_answers
from overwatch.decisions.tasks import d12_pursuit_route, prepare
from overwatch.sim.worlds.pursuit import street_graph

STREETS = street_graph()
FENCE = [(-156, -146), (236, -146), (236, 146), (-156, 146)]


def test_snap_finds_the_street():
    (a, b), q, d = snap(STREETS, (35.0, 40.0))
    assert {a, b} == {"+30,+0", "+30,+95"} and d == 5.0 and tuple(q) == (30.0, 40.0)


def test_running_north_offers_the_north_end_and_momentum_after_stopping():
    r = route_options(STREETS, (35.5, 32.0), (0.0, 4.5), 4.0, (31.0, 22.0), 6.0, FENCE)
    streets = [x for x in r if x["kind"] == "street"]
    assert streets and all(x["waypoints"][-1][1] > 32.0 for x in streets)  # only northwards while running north
    # stopped (hiding) after sprinting north: both ends offered, the north one weighted by momentum
    r2 = route_options(STREETS, (35.5, 32.0), (0.0, 0.0), 6.0, (31.0, 22.0), 6.0, FENCE, prior_vel=(0.0, 4.5))
    north = sum(x["share"] for x in r2 if x["kind"] == "street" and x["waypoints"][-1][1] > 32.0)
    south = sum(x["share"] for x in r2 if x["kind"] == "street" and x["waypoints"][-1][1] < 32.0)
    assert north > 2 * south > 0
    assert abs(sum(x["share"] for x in r2) - 1.0) < 1e-6


def test_branches_at_the_intersection_and_geofence():
    r = route_options(STREETS, (30.0, 80.0), (0.0, 3.0), 10.0, (30.0, 60.0), 6.0, FENCE)
    names = {x["description"] for x in r}
    assert {"At intersection +30,+95, check left", "At intersection +30,+95, check right",
            "At intersection +30,+95, check straight on"} <= names
    tight = [(-40, -40), (40, -40), (40, 100), (-40, 100)]
    r = route_options(STREETS, (30.0, 80.0), (0.0, 3.0), 10.0, (30.0, 60.0), 6.0, tight)
    assert any(x["unsafe"] == "leaves the geofence" for x in r)


def test_d12_rules_pick_and_unsafe_removal():
    routes = route_options(STREETS, (30.0, 80.0), (0.0, 3.0), 10.0, (30.0, 60.0), 6.0,
                           [(-40, -40), (40, -40), (40, 100), (-40, 100)])
    job = prepare(d12_pursuit_route({"time_since_loss_s": 10.0, "routes": routes, "operator_request": None}))
    offered = {o.value for o in job.questions[0].options}
    assert all(r["id"] not in offered for r in routes if r["unsafe"]) and "hold" in offered
    pick = next(a.choice for a in rule_answers(job) if a.name == "route")
    assert pick in offered
    long = prepare(d12_pursuit_route({"time_since_loss_s": 60.0, "routes": routes}))
    assert next(a.choice for a in rule_answers(long) if a.name == "route") == "hold"


def test_describes_clothing_words_only():
    assert describes("look for red shirt, jeans", ("red", "blue")) == 1.5  # red top +1, jeans (blue lower) +0.5
    assert describes("red shirt", ("blue", "black")) == 0.0
    assert describes("", ("red", "blue")) == 0.0


class SimLink:
    is_sim = True

    def connected(self, now_ms):
        return True

    def send(self, cmd):
        pass


def inputs(now, verified, box=(460.0, 250.0, 500.0, 330.0)):
    return AutonomyInputs(now_ms=now, subject_state="LOCKED", subject_box=box, image_size=(960, 540), hfov_deg=82.0,
                          vfov_deg=52.0, subject_xy=(10.0, 30.0), subject_vel=(0.0, 2.0), last_seen_xy=(10.0, 30.0),
                          lost_s=None, drone=DroneSafetyState((0.0, 0.0), 24.0, 0.0, 90.0, True), gimbal_pitch_deg=-60.0,
                          calibration_valid=True, subject_verified=verified)


def test_unverified_rebind_holds_position_and_route_flies_towards_waypoint():
    cfg = load_config().autonomy
    cfg.geofence = []
    a = AutonomyController(cfg, SimLink())
    a.set_mode("FOLLOW", 0.0)
    st, ev = a.tick(inputs(50.0, verified=False))
    assert st.fsm == "VERIFY" and any(e["kind"] == "autonomy_verify" for e in ev)
    assert st.last_command.forward_mps == 0.0 and st.last_command.right_mps == 0.0  # camera only, no chasing
    st, _ = a.tick(inputs(100.0, verified=True))
    assert st.fsm == "TRACKING"
    a.set_route("P2", [(0.0, 20.0)], "check north", "decision:test", 150.0)
    lost = inputs(200.0, verified=True)
    lost.subject_state, lost.subject_box, lost.lost_s = "REACQUIRE", None, 4.0
    st, _ = a.tick(lost)
    assert st.fsm == "REACQUIRE" and st.primitive == "route:P2" and st.route == [(0.0, 20.0)]
    assert st.last_command.forward_mps > 0.5  # heading 0 = north: flying towards the waypoint
    assert np.isclose(st.last_command.right_mps, 0.0, atol=0.05)
