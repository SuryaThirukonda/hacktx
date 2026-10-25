import numpy as np
import pytest

from overwatch.config import PhoneCfg
from overwatch.geometry.camera import Intrinsics
from overwatch.geometry.phone_depth import ARKIT_FROM_CV, DepthFrame, PhoneDepthProcessor

INTR = Intrinsics.from_hfov(256, 192, 64.0)


def floor_depth(cam_h=1.3, pitch_deg=-20.0, wall_at=None):
    """Depth of a flat floor (and optional wall) for a level-ish camera looking forward along ARKit -z."""
    W, H = 256, 192
    uu, vv = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    x = (uu - INTR.cx) / INTR.fx
    y = (vv - INTR.cy) / INTR.fy
    p = np.radians(pitch_deg)
    # camera ray in a y-down, z-forward frame tilted by pitch: world_down component
    down = y * np.cos(p) - np.sin(p)  # positive -> towards the floor
    fwd = np.cos(p) + y * np.sin(p)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_floor = np.where(down > 1e-6, cam_h / down, np.inf)
    t = t_floor
    if wall_at is not None:
        t_wall = np.where(fwd > 1e-6, wall_at / fwd, np.inf)
        t = np.minimum(t, t_wall)
    depth = np.where(np.isfinite(t), t, 0.0).astype(np.float32)
    return depth, x


def pose(pitch_deg=-20.0, cam_h=1.3):
    """ARKit world-from-camera for a camera at height cam_h looking along ARKit -z, pitched down."""
    p = np.radians(pitch_deg)
    x_axis = [1.0, 0.0, 0.0]  # OpenCV camera axes expressed in ARKit world (y up)
    y_axis = [0.0, -np.cos(p), -np.sin(p)]
    z_axis = [0.0, np.sin(p), -np.cos(p)]
    R_cv_in_world = np.column_stack([x_axis, y_axis, z_axis])
    T = np.eye(4)
    T[:3, :3] = R_cv_in_world @ ARKIT_FROM_CV
    T[:3, 3] = [0.0, cam_h, 0.0]
    return T


def frame(depth):
    conf = np.full(depth.shape, 2, np.uint8)
    conf[depth <= 0] = 0
    return DepthFrame(depth, conf, INTR.K, pose(), 0.0)


def test_floor_only_produces_no_obstacle():
    proc = PhoneDepthProcessor(PhoneCfg())
    depth, _ = floor_depth()
    for _ in range(3):
        r = proc.process(frame(depth))
    assert r.corridor_nearest_m is None
    assert not r.proximity_alert
    assert r.floor_y_m == pytest.approx(0.0, abs=0.06)
    front = next(s for s in r.sectors if s.sector == "front")
    assert front.nearest_m is None and front.confidence != "unknown"


def test_wall_ahead_triggers_proximity_after_persistence():
    cfg = PhoneCfg(proximity_alert_m=1.2, persistence_frames=2)
    proc = PhoneDepthProcessor(cfg)
    depth, _ = floor_depth(wall_at=0.9)
    r1 = proc.process(frame(depth))
    assert r1.corridor_nearest_m == pytest.approx(0.9, abs=0.08)
    assert not r1.proximity_alert  # needs persistence
    r2 = proc.process(frame(depth))
    assert r2.proximity_alert
    front = next(s for s in r2.sectors if s.sector == "front")
    assert front.nearest_m == pytest.approx(0.9, abs=0.15)
    left = next(s for s in r2.sectors if s.sector == "left")
    assert left.confidence == "unknown"  # outside the 64 deg field of view


def test_invalid_depth_reports_unknown():
    proc = PhoneDepthProcessor(PhoneCfg())
    depth = np.zeros((192, 256), np.float32)
    r = proc.process(frame(depth))
    assert r.valid_fraction == 0.0
    assert all(s.confidence == "unknown" and s.nearest_m is None for s in r.sectors)
