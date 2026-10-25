import numpy as np
import pytest

from overwatch.autonomy.safety import DroneSafetyState, SafetySupervisor, fence_margin
from overwatch.autonomy.vstick import to_dji
from overwatch.config import AutonomyCfg
from overwatch.schemas import BridgeCommand, VStickCommand
from overwatch.sim.bridge_sim import SimBridge
from overwatch.sim.drone_dynamics import SimDrone


def test_vstick_axis_mapping():
    # DJI body-frame velocity mode: +roll = forward, +pitch = right
    s = to_dji(forward=1.0, right=0.0, up=0.0, yaw_rate=0.0)
    assert (s.roll, s.pitch) == (1.0, 0.0)
    s = to_dji(forward=0.0, right=1.0, up=0.0, yaw_rate=0.0)
    assert (s.roll, s.pitch) == (0.0, 1.0)
    s = to_dji(forward=99, right=-99, up=9, yaw_rate=500)
    assert (s.roll, s.pitch, s.throttle, s.yaw) == (15.0, -15.0, 4.0, 100.0)  # SDK limits


FENCE = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]


def test_fence_margin_sign():
    assert fence_margin(FENCE, 0, 0) == pytest.approx(10.0)
    assert fence_margin(FENCE, 12, 0) == pytest.approx(-2.0)


def test_safety_geofence_removes_outward_motion():
    sup = SafetySupervisor(AutonomyCfg(geofence=FENCE))
    st = DroneSafetyState(xy=(9.0, 0.0), alt_m=18.0, heading_deg=90.0, battery_pct=80.0, link_ok=True)
    r = sup.apply(forward=4.0, right=0.0, up=0.0, yaw_rate=0.0, st=st, people_xy=[], reacquiring=False)
    assert r.forward < 0.5  # flying east toward the fence edge at x=10 is blocked
    assert any("geofence" in c for c in r.status.clamps)


def test_safety_speed_altitude_and_people_over():
    sup = SafetySupervisor(AutonomyCfg(geofence=FENCE, min_altitude_m=12.0))
    st = DroneSafetyState(xy=(0.0, 0.0), alt_m=10.0, heading_deg=0.0, battery_pct=80.0, link_ok=True)
    r = sup.apply(forward=9.0, right=0.0, up=-1.0, yaw_rate=200.0, st=st, people_xy=[(0.5, 0.0)], reacquiring=False)
    assert abs(r.yaw_rate) <= 45.0
    assert r.up > 0  # below the altitude floor: climb
    assert r.status.people_over
    assert r.right < 0  # pushed away from the person at +x


def test_battery_rtl_requested():
    sup = SafetySupervisor(AutonomyCfg(geofence=FENCE))
    st = DroneSafetyState(xy=(0.0, 0.0), alt_m=18.0, heading_deg=0.0, battery_pct=15.0, link_ok=True)
    assert sup.apply(0, 0, 0, 0, st, [], False).request_rtl


def _bridge():
    d = SimDrone(0, 0, 18, 0)
    return SimBridge(d, ttl_ms=300, link_timeout_ms=2000, latency_ms=(0.0, 0.0)), d


def test_watchdog_hovers_after_ttl_and_disables_after_link_loss():
    br, d = _bridge()
    br.send(BridgeCommand(type="mode", payload={"vstick": True}))
    br.step(0.05, 0.0)
    br.send(BridgeCommand(type="vstick", payload=VStickCommand(forward_mps=3.0).model_dump()))
    t = 0.0
    for _ in range(20):  # 1 s of commands at 20 Hz
        t += 50
        br.send(BridgeCommand(type="vstick", payload=VStickCommand(forward_mps=3.0, ts_ms=t).model_dump()))
        br.step(0.05, t)
    assert d.vel[1] > 2.0  # moving north at ~3 m/s
    for _ in range(10):  # stop sending: 500 ms
        t += 50
        br.step(0.05, t)
    assert any(a["kind"] == "watchdog_hover" for a in br.audit)
    for _ in range(40):
        t += 50
        br.step(0.05, t)
    assert abs(d.vel[1]) < 0.3  # hovering
    assert not d.vstick_enabled  # >2 s without the Mac: pilot regains control
    assert any(a["kind"] == "link_lost" for a in br.audit)


def test_pilot_override_exits_virtual_stick():
    br, d = _bridge()
    br.send(BridgeCommand(type="mode", payload={"vstick": True}))
    br.step(0.05, 0.0)
    assert d.vstick_enabled
    br.pilot_override()
    assert not d.vstick_enabled
