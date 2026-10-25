"""Relative visual follow: signed movement, actual gimbal output and all stop conditions."""
from dataclasses import replace
import math
import pytest

from overwatch.autonomy.controller import AutonomyController, AutonomyInputs
from overwatch.autonomy.safety import DroneSafetyState
from overwatch.config import AutonomyCfg


class Bridge:
    is_sim = False
    control_capable = True
    control_inhibited = False
    def __init__(self): self.messages = []
    def connected(self, now): return True
    def send(self, message): self.messages.append(message)


def setup():
    bridge = Bridge()
    control = AutonomyController(AutonomyCfg(motion_profile="visual_follow", allow_real_flight=True), bridge)
    control.set_mode("FOLLOW", 0)
    inp = AutonomyInputs(now_ms=0, subject_state="LOCKED", subject_box=(430, 170, 570, 430),
        image_size=(1000, 600), hfov_deg=80, vfov_deg=55, subject_xy=None, subject_vel=None,
        last_seen_xy=None, lost_s=None, drone=DroneSafetyState(None, 2, 0, 85, True),
        gimbal_pitch_deg=-20, subject_track_id="person-A")
    return control, bridge, inp


def settle(control, inp, start=50, frames=16):
    for i in range(frames): state, _ = control.tick(replace(inp, now_ms=start+i*50))
    return state


def test_relative_forward_backward_and_strafe_do_not_need_fake_ground_coordinates():
    c, bridge, inp = setup()
    settle(c, inp)
    assert bridge.messages[0].payload["motion_profile"] == "full"  # existing guarded SDK wire profile
    smaller = replace(inp, subject_box=(440, 190, 560, 410))
    state = settle(c, smaller, 1000)
    assert state.last_command.forward_mps > 0
    assert state.last_command.right_mps == 0 and state.last_command.up_mps == 0
    larger = replace(inp, subject_box=(420, 140, 580, 460))
    state = settle(c, larger, 2000)
    assert state.last_command.forward_mps < 0
    state = settle(c, replace(inp, subject_box=(600, 170, 740, 430)), 3000)
    assert state.last_command.right_mps > 0 and state.last_command.yaw_rate_dps > 0
    state = settle(c, replace(inp, subject_box=(260, 170, 400, 430)), 4000)
    assert state.last_command.right_mps < 0 and state.last_command.yaw_rate_dps < 0
    assert all(m.payload.get("up_mps", 0) == 0 for m in bridge.messages)


@pytest.mark.parametrize("changes", [
    {"subject_state":"LOST", "subject_box":None}, {"subject_verified":False}, {"video_fresh":False},
    {"subject_box":(0,100,160,400)}, {"occluder_near_last_seen":True},
])
def test_lost_unverified_stale_clipped_or_occluded_stops_translation(changes):
    c, _, inp = setup(); settle(c, inp)
    moving = replace(inp, subject_box=(600,170,740,430))
    assert settle(c, moving, 1000).last_command.right_mps > 0
    state = settle(c, replace(moving, **changes), 2000)
    assert state.last_command.forward_mps == state.last_command.right_mps == state.last_command.up_mps == 0


def test_camera_pitch_centers_vertically_and_stops_with_target_loss():
    c, b, inp = setup()
    above = replace(inp, subject_box=(430,80,570,340))
    state = settle(c, above)
    assert state.gimbal_cmd_deg > -20
    assert any(m.type == "gimbal" and m.payload["pitch_deg"] > -20 for m in b.messages)
    c.set_mode("FOLLOW", 1000)
    below = replace(inp, subject_box=(430,260,570,520))
    state = settle(c, below, 1100)
    assert state.gimbal_cmd_deg < -20
    state, _ = c.tick(replace(inp, now_ms=2200, subject_state="LOST", subject_box=None))
    assert state.gimbal_cmd_deg is None
    assert state.last_command.yaw_rate_dps == 0


def test_manual_diagonal_speed_cap_key_expiry_and_rc_priority():
    c, b, inp = setup(); session = c.start_keyboard(0)
    c.keyboard_input(session, 1, yaw=1, tilt=-1, now_ms=50, forward=1, right=1)
    state, _ = c.tick(replace(inp, now_ms=50, subject_box=None, subject_state="UNLOCKED"))
    assert 0 < state.last_command.forward_mps and 0 < state.last_command.right_mps
    assert math.hypot(state.last_command.forward_mps, state.last_command.right_mps) <= 0.5001
    assert state.last_command.up_mps == 0 and state.gimbal_cmd_deg < -20
    state, _ = c.tick(replace(inp, now_ms=351))
    assert state.mode == "MANUAL" and c.keyboard_session is None
    session = c.start_keyboard(400)
    b.control_inhibited = True
    state, _ = c.tick(replace(inp, now_ms=450))
    assert state.mode == "MANUAL" and c.keyboard_session is None
    with pytest.raises(ValueError): c.keyboard_input(session, 2, 0, 0, 460, 1, 0)


def test_explicit_new_subject_or_restart_resets_starting_size_reference():
    c, _, inp = setup(); settle(c, inp)
    smaller = replace(inp, subject_box=(440,190,560,410))
    assert settle(c, smaller, 1000).last_command.forward_mps > 0
    state = settle(c, replace(smaller, subject_track_id="person-B"), 2000)
    assert state.last_command.forward_mps == 0
    c.set_mode("FOLLOW", 3000)
    assert c.visual_reference_height is None


def test_no_forward_approach_when_subject_is_far_off_center():
    c, _, inp = setup(); settle(c, inp)
    state = settle(c, replace(inp, subject_box=(730,190,850,410)), 1000)
    assert state.last_command.forward_mps == 0 and state.last_command.right_mps > 0
