import pytest

from overwatch.schemas import VStickCommand

mujoco = pytest.importorskip("mujoco")


def _fly(d, sec, cmd, dt=1 / 30):
    for _ in range(int(sec / dt)):
        d.step(dt, cmd)


def test_x2_tracks_virtual_stick_setpoints():
    from overwatch.sim.mujoco_drone import MujocoDrone

    d = MujocoDrone(10.0, -9.0, 18.0, 0.0, -52.0)
    d.vstick_enabled = True
    _fly(d, 2, VStickCommand())
    assert abs(d.pos[2] - 18.0) < 0.3 and abs(d.heading) % 360 < 1.0
    _fly(d, 3, VStickCommand(forward_mps=4.0))  # heading 0 = north = +y
    assert d.vel[1] == pytest.approx(4.0, abs=0.3) and d.pitch < -5  # nose down into the direction of travel
    h0 = d.heading
    _fly(d, 2, VStickCommand(yaw_rate_dps=30.0))
    assert (d.heading - h0) % 360 == pytest.approx(60.0, abs=6.0)  # clockwise, like the DJI convention
    _fly(d, 3, VStickCommand())
    assert abs(d.vel[0]) < 0.2 and abs(d.vel[1]) < 0.2


def test_scenario_uses_mujoco_and_keeps_kinematic_default():
    from overwatch.config import load_config
    from overwatch.faults import FaultInjector
    from overwatch.sim.driver import SimulationDriver

    assert type(SimulationDriver(load_config(), FaultInjector(), "OW7").drone).__name__ == "MujocoDrone"
    assert type(SimulationDriver(load_config(), FaultInjector(), "OW1").drone).__name__ == "SimDrone"
