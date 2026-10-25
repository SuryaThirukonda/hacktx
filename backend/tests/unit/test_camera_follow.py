"""Camera-only flight invariants, including cases where the full controller would climb or translate."""
from dataclasses import replace

import pytest

from overwatch.autonomy.controller import AutonomyController, AutonomyInputs
from overwatch.autonomy.safety import DroneSafetyState
from overwatch.config import AutonomyCfg


class Bridge:
    is_sim = True
    def __init__(self):
        self.messages = []
    def connected(self, now):
        return True
    def send(self, command):
        self.messages.append(command)


def inputs(now=50, **changes):
    original = AutonomyInputs(now_ms=now, subject_state="LOCKED", subject_box=(700, 350, 800, 500),
                             image_size=(960, 540), hfov_deg=72, vfov_deg=50, subject_xy=(30, 30),
                             subject_vel=(4, 3), last_seen_xy=(30, 30), lost_s=None,
                             drone=DroneSafetyState((0, 0), 1.5, 0, 80, True), gimbal_pitch_deg=-30,
                             people_xy=[(.2, .1)], calibration_valid=True)
    return replace(original, **changes)


def controller():
    bridge = Bridge()
    control = AutonomyController(AutonomyCfg(motion_profile="camera_only"), bridge)
    control.set_mode("FOLLOW", 0)
    return control, bridge


def assert_zero_translation(command):
    assert (command.forward_mps, command.right_mps, command.up_mps) == (0, 0, 0)


def test_only_yaw_and_gimbal_even_below_altitude_floor_and_over_people():
    control, bridge = controller()
    state, _ = control.tick(inputs())
    assert state.fsm == "TRACKING" and 0 < state.last_command.yaw_rate_dps <= 15
    assert state.gimbal_cmd_deg < -30
    assert_zero_translation(state.last_command)
    assert bridge.messages[0].payload["motion_profile"] == "camera_only"
    for ms in range(100, 1000, 50):
        state, _ = control.tick(inputs(ms))
        assert_zero_translation(state.last_command)
    assert any(m.type == "gimbal" for m in bridge.messages)


@pytest.mark.parametrize("changes", [
    {"subject_state": "REACQUIRE", "subject_box": None, "lost_s": 5},
    {"subject_verified": False}, {"video_fresh": False},
])
def test_lost_unverified_and_stale_targets_stop_rotation(changes):
    control, _ = controller()
    control.tick(inputs())
    state, _ = control.tick(inputs(100, **changes))
    assert state.last_command.yaw_rate_dps == 0
    assert state.gimbal_cmd_deg is None
    assert_zero_translation(state.last_command)
    state, _ = control.tick(inputs(150))
    assert state.fsm == "TRACKING" and state.last_command.yaw_rate_dps > 0


def test_routes_cannot_bypass_camera_only_and_low_battery_does_not_command_rtl():
    control, bridge = controller()
    control.set_route("route", [(40, 30)], "move", "decision:openai", 1)
    state, _ = control.tick(inputs())
    assert not state.route
    assert_zero_translation(state.last_command)
    state, _ = control.tick(inputs(100, drone=DroneSafetyState((0, 0), 18, 0, 10, True)))
    assert state.mode == "MANUAL"
    assert not any(m.type == "rtl" for m in bridge.messages)


def test_direction_and_center_deadband():
    control, _ = controller()
    state, _ = control.tick(inputs(subject_box=(100, 220, 200, 320)))
    assert state.last_command.yaw_rate_dps < 0
    state, _ = control.tick(inputs(100, subject_box=(450, 240, 510, 300)))
    assert state.last_command.yaw_rate_dps == 0


def test_keyboard_moves_without_target_and_release_stops_without_auto_follow():
    control, bridge = controller()
    session = control.start_keyboard(0)
    control.keyboard_input(session, 1, -1, 1, 10)
    state, _ = control.tick(inputs(50, subject_box=None, subject_state="EMPTY"))
    assert state.mode == "HOLD" and state.last_command.yaw_rate_dps == -15
    assert state.gimbal_cmd_deg == pytest.approx(-29.5)
    assert_zero_translation(state.last_command)
    control.keyboard_input(session, 2, 0, 0, 60)
    state, _ = control.tick(inputs(100))
    assert state.last_command.yaw_rate_dps == 0 and state.gimbal_cmd_deg is None
    control.stop_keyboard(session, 110)
    assert control.mode == "MANUAL"
    assert bridge.messages[-1].type == "mode" and bridge.messages[-1].payload["vstick"] is False
    with pytest.raises(ValueError):
        control.keyboard_input(session, 3, 1, 1, 120)


@pytest.mark.parametrize("failure", ["timeout", "stale_video", "rc"])
def test_keyboard_failure_requires_new_explicit_session(failure):
    control, bridge = controller()
    session = control.start_keyboard(0)
    control.keyboard_input(session, 1, 1, -1, 10)
    control.tick(inputs(50))
    if failure == "rc":
        bridge.control_inhibited = True
    control.tick(inputs(400 if failure == "timeout" else 100, video_fresh=failure != "stale_video"))
    assert control.mode == "MANUAL" and control.keyboard_session is None
    bridge.control_inhibited = False
    with pytest.raises(ValueError):
        control.keyboard_input(session, 2, 1, 1, 450)
    new_session = control.start_keyboard(500)
    assert new_session != session
    control.stop_keyboard(session, 501)  # an old browser tab cannot cancel the new owner
    assert control.keyboard_session == new_session


def test_keyboard_rejects_duplicate_reordered_and_invalid_input_and_full_profile():
    control, _ = controller()
    session = control.start_keyboard(0)
    control.keyboard_input(session, 2, 0, 0, 10)
    for seq, yaw in [(1, 1), (2, 1), (3, float("nan")), (3, 2)]:
        with pytest.raises(ValueError):
            control.keyboard_input(session, seq, yaw, 0, 20)
    with pytest.raises(ValueError):
        control.start_keyboard(20)  # no takeover by a second browser session
    control.set_mode("FOLLOW", 30)
    assert control.keyboard_session is None
    control.cfg.motion_profile = "full"
    with pytest.raises(ValueError):
        control.start_keyboard(40)


def test_physical_full_follow_translates_but_stops_on_loss_and_rc_override():
    control, bridge = controller()
    bridge.is_sim = False
    control.cfg.allow_real_flight = True
    control.cfg.motion_profile = "full"
    control.cfg.follow_max_speed_mps = 1
    control.cfg.reacquire_max_speed_mps = 1
    control.cfg.max_vertical_mps = .5
    control.set_mode("FOLLOW", 0)
    inp = inputs(50, drone=DroneSafetyState((0, 0), 18, 0, 80, True), subject_xy=(0, 30),
                 subject_vel=(0.5, 1), people_xy=[])
    state, _ = control.tick(inp)
    assert 0 < state.last_command.forward_mps <= 1
    assert state.last_command.right_mps > 0
    assert abs(state.last_command.up_mps) <= .5
    for loss in [{"subject_box": None, "subject_state": "REACQUIRE"}, {"subject_verified": False}, {"video_fresh": False}]:
        control.set_route("search", [(20, 30)], "search", "model", 51)
        state, _ = control.tick(replace(inp, now_ms=100+len(bridge.messages)*50, **loss))
        assert_zero_translation(state.last_command)
        assert state.last_command.yaw_rate_dps == 0 and not state.route and state.gimbal_cmd_deg is None
    bridge.control_inhibited = True
    state, _ = control.tick(replace(inp, now_ms=1000))
    assert state.mode == "MANUAL" and control.keyboard_session is None
