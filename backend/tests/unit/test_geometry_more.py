import math

import numpy as np
import pytest

from overwatch.config import RegistrationCfg
from overwatch.geometry.camera import CameraPose, Intrinsics, PinholeCamera
from overwatch.geometry.homography import fit_marker_homography, marker_world_corners
from overwatch.geometry.kalman import CAFilter
from overwatch.geometry.kinematics import KinematicsTracker, clock_position
from overwatch.geometry.registration import PhoneRegistration, kabsch_2d
from overwatch.vision.markers import MarkerDetection

LAYOUT = {0: (0.0, 0.0), 1: (20.0, 0.0), 2: (20.0, 12.0), 3: (0.0, 12.0), 4: (10.0, 0.0), 5: (10.0, 12.0)}


def _dets(cam, ids, noise_px=0.0, rng=None):
    out = []
    for i in ids:
        w = marker_world_corners(LAYOUT[i], 1.0)
        uv, _ = cam.project(np.column_stack([w, np.zeros(4)]))
        if noise_px:
            uv = uv + rng.normal(0, noise_px, uv.shape)
        out.append(MarkerDetection(i, uv))
    return out


CAM = PinholeCamera(Intrinsics.from_hfov(960, 540, 71.6), CameraPose(np.array([10.0, 6.0, 20.0]), 0.0, -90.0))


def test_marker_homography_exact_holdout_is_zero():
    fit = fit_marker_homography(_dets(CAM, range(6)), LAYOUT, 1.0, {4, 5})
    assert fit.valid and fit.holdout_method == "dedicated"
    assert fit.holdout_rmse_m < 1e-3


def test_marker_homography_noisy_holdout_reports_real_error():
    rng = np.random.default_rng(0)
    fit = fit_marker_homography(_dets(CAM, range(6), 1.5, rng), LAYOUT, 1.0, {4, 5})
    assert fit.valid
    assert 0.005 < fit.holdout_rmse_m < 0.3


def test_marker_homography_needs_markers_and_holdout():
    assert not fit_marker_homography(_dets(CAM, [0, 1]), LAYOUT, 1.0, {4, 5}).valid
    f = fit_marker_homography(_dets(CAM, [0, 1, 2]), LAYOUT, 1.0, {4, 5})
    assert not f.valid and "holdout" in f.reason  # 3 fit markers, no holdout visible, LOO needs 4
    f2 = fit_marker_homography(_dets(CAM, [0, 1, 2, 3]), LAYOUT, 1.0, {4, 5})
    assert f2.valid and f2.holdout_method == "leave_one_out"


def test_kabsch_recovers_rigid_transform():
    rng = np.random.default_rng(1)
    A = rng.uniform(-5, 5, (30, 2))
    th = math.radians(37.0)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    B = A @ R.T + np.array([3.0, -2.0])
    R2, t2, rms = kabsch_2d(A, B)
    assert R2 == pytest.approx(R, abs=1e-9) and t2 == pytest.approx([3.0, -2.0], abs=1e-9) and rms < 1e-9


def test_registration_needs_travel_then_aligns():
    reg = PhoneRegistration(RegistrationCfg(min_pairs=10, min_travel_m=2.0, max_residual_m=0.5))
    th = math.radians(-60)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    for i in range(20):
        a = np.array([0.02 * i, 0.01 * i])  # 0.4 m of travel: not enough
        reg.add_pair(i * 100.0, a, tuple(R @ a + [5, 5]), 0.2)
    assert not reg.solve(2000.0).valid
    for i in range(20, 60):
        a = np.array([0.1 * i, 0.05 * i])
        reg.add_pair(i * 100.0, a, tuple(R @ a + [5, 5]), 0.2)
    st = reg.solve(6000.0)
    assert st.valid and st.residual_m < 0.05
    out = reg.to_local(np.array([[1.0, 2.0]]))
    assert out[0] == pytest.approx(R @ [1.0, 2.0] + [5, 5], abs=1e-6)


def test_closing_speed_three_mps():
    kin = KinematicsTracker(0.8, 5)
    kf = None
    res = None
    for i in range(40):  # subject runs from y=20 toward a responder at the origin at 3 m/s, 15 Hz
        t = i * 66.7
        y = 20.0 - 3.0 * t / 1000.0
        if kf is None:
            kf = CAFilter((0.0, y), t, 0.2)
        else:
            kf.update((0.0, y), t, 0.2)
        res = kin.compute(t, kf, 0.2, (0.0, 0.0), 0.2, 0.0, True, True, True)
    assert res.valid
    assert res.closing_mps == pytest.approx(3.0, abs=0.2)
    assert res.clock == 12 and res.alignment_cos == pytest.approx(1.0, abs=0.05)
    assert res.ttc_s == pytest.approx(res.range_m / res.closing_mps, rel=0.05)


def test_clock_positions():
    assert clock_position(0) == 12 and clock_position(60) == 2 and clock_position(-90) == 9 and clock_position(175) == 6


def test_kinematics_refuses_without_calibration_or_responder():
    kf = CAFilter((0, 5), 0, 0.2)
    k = KinematicsTracker()
    assert not k.compute(0, kf, 0.2, (0, 0), 0.2, 0, True, True, calib_valid=False).valid
    assert not k.compute(0, kf, 0.2, None, None, 0, True, True, True).valid
    assert not k.compute(0, kf, 0.2, (0, 0), 0.2, 0, subject_fresh=False, common_frame=True, calib_valid=True).valid
