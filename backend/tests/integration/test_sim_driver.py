from overwatch.config import load_config
from overwatch.faults import FaultInjector
from overwatch.sim.driver import SimulationDriver


def test_driver_emits_all_sensor_streams():
    cfg = load_config()
    drv = SimulationDriver(cfg, FaultInjector(), "demo")
    frames = phones = tels = 0
    actions = []
    for _ in range(int(3.0 * 30)):
        out = drv.step(1 / 30)
        frames += out.drone_frame is not None
        phones += out.phone is not None
        tels += out.telemetry is not None
        actions += out.actions
    assert 40 <= frames <= 47  # 15 FPS
    assert 27 <= phones <= 31  # 10 Hz
    assert 28 <= tels <= 31
    assert any(a.action == "lock_responder" for a in actions)
    assert out.drone_frame is None or out.drone_frame.gt is not None


def test_frame_drop_fault_drops_frames():
    cfg = load_config()
    faults = FaultInjector()
    drv = SimulationDriver(cfg, faults, "S01")
    faults.set_manual("drone_video_drop", True)
    assert all(drv.step(1 / 30).drone_frame is None for _ in range(30))
    faults.set_manual("drone_video_drop", False)
    assert any(drv.step(1 / 30).drone_frame is not None for _ in range(30))
