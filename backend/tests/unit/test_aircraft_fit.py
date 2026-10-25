"""Phase 15: aircraft profiles with sources, Dryden statistics, the energy battery, and the step-response harness.

The DJI fit test replays recorded velocity steps when they exist (data/recordings/dji_steps/*.csv). None have been
recorded yet (it needs the Mini 2 and a working bridge), so that test is skipped with the reason, never faked."""

import math

import numpy as np
import pytest

from overwatch.sim.aircraft import DJI_MINI_2
from overwatch.sim.drone_dynamics import DroneParams, SimDrone
from overwatch.sim.step_response import RECORDINGS, compare, load_recording, metrics, run_model, step_commands
from overwatch.sim.wind import DrydenWind, EnergyBattery, gust_statistics


def test_every_profile_value_has_a_source_and_defaults_match_the_mini_2_profile():
    for row in DJI_MINI_2.table():
        assert row["source"] in ("spec sheet", "Overwatch cap", "assumed", "measured"), row
    assert DJI_MINI_2.values["max_ascent"].source == "spec sheet"
    assert DJI_MINI_2.values["tau_v"].source == "assumed"  # not measured: said so
    assert DJI_MINI_2.params() == DroneParams()  # the kinematic defaults are this profile, nothing hidden


def test_kinematic_step_response_matches_its_time_constant():
    d = SimDrone(0, 0, 20, 0.0)
    s = run_model(d, step_commands([(1.0, 5.0)]), 8.0)
    m = metrics(s)
    assert abs(m["steady_mps"] - 5.0) < 0.05
    assert abs(m["rise_s"] - 2.197 * DroneParams().tau_v) < 0.15  # first order: 10-90% rise = ln(9) tau


def test_compare_passes_on_matching_and_fails_on_slow_models():
    ref = run_model(SimDrone(0, 0, 20, 0.0), step_commands([(1.0, 5.0)]), 8.0)
    same = run_model(SimDrone(0, 0, 20, 0.0), step_commands([(1.0, 5.0)]), 8.0)
    slow = run_model(SimDrone(0, 0, 20, 0.0, params=DroneParams(tau_v=1.2)), step_commands([(1.0, 5.0)]), 8.0)
    assert compare(same, ref)["pass"]
    c = compare(slow, ref)
    assert not c["pass"] and c["rise_rel_err"] > 0.15


def test_dryden_statistics():
    w = DrydenWind(w20_mps=8.0, alt_m=30.0, airspeed_mps=6.0, seed=3)
    sig, L = w.sigmas_and_scales()
    xs = np.array([w.step(0.05) for _ in range(40000)])
    st = gust_statistics(xs)
    assert abs(st["std_u"] - sig[0]) / sig[0] < 0.2 and abs(st["std_w"] - sig[2]) / sig[2] < 0.2
    assert sig[2] == pytest.approx(0.8, rel=1e-6)  # sigma_w = 0.1 * W20
    assert st["lag1_u"] == pytest.approx(math.exp(-6.0 * 0.05 / L[0]), abs=0.02)


def test_energy_battery_hover_matches_the_profile():
    b = EnergyBattery(capacity_wh=DJI_MINI_2.v("battery_energy"), hover_w=DJI_MINI_2.v("hover_power"))
    minutes = b.minutes_left(0.0)
    assert minutes == pytest.approx(31.0, rel=0.01)  # hover power was derived from the spec flight time
    for _ in range(600):
        b.step(1.0, speed_mps=8.0)
    assert b.percent() < 100 - 600 / (31 * 60) * 100  # flying fast drains faster than hovering


def test_dji_recordings_fit():
    files = sorted(RECORDINGS.glob("*.csv")) if RECORDINGS.exists() else []
    if not files:
        pytest.skip("no DJI simulator step recordings yet (hardware-dependent: Mini 2 + bridge, props off)")
    for f in files:
        ref = load_recording(f)
        levels = []
        for t, c, _ in ref:
            if not levels or levels[-1][1] != c:
                levels.append((t, c))
        sim = run_model(SimDrone(0, 0, 20, 0.0), step_commands(levels), float(ref[-1, 0]))
        assert compare(sim, ref)["pass"], f.name
