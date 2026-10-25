import math

import numpy as np
import pytest

from overwatch.geometry.camera import CameraPose, Intrinsics, PinholeCamera, heading_from_vector, wrap_deg

INTR = Intrinsics.from_hfov(1280, 720, 71.6)


def cam(x=0.0, y=0.0, h=20.0, heading=0.0, pitch=-90.0, roll=0.0):
    return PinholeCamera(INTR, CameraPose(np.array([x, y, h]), heading, pitch, roll))


def test_intrinsics_from_mini2_hfov():
    assert INTR.fx == pytest.approx(886.9, abs=1.0)
    assert INTR.vfov_deg == pytest.approx(44.2, abs=0.3)


def test_ground_projection_nadir_center_is_drone_ground_point():
    c = cam(3.0, -2.0)
    pts, hit = c.pixel_to_ground(np.array([[INTR.cx, INTR.cy]]))
    assert hit.all()
    assert pts[0] == pytest.approx([3.0, -2.0], abs=1e-6)


def test_ground_projection_nadir_right_edge():
    c = cam(h=20.0)
    pts, _ = c.pixel_to_ground(np.array([[INTR.width, INTR.cy]]))
    assert pts[0, 0] == pytest.approx(20.0 * math.tan(math.radians(71.6 / 2)), rel=1e-3)
    assert pts[0, 1] == pytest.approx(0.0, abs=1e-6)


def test_ground_projection_oblique_center_lands_20m_ahead():
    c = cam(h=20.0, pitch=-45.0)
    pts, _ = c.pixel_to_ground(np.array([[INTR.cx, INTR.cy]]))
    assert pts[0] == pytest.approx([0.0, 20.0], abs=1e-6)


def test_heading_east_rotates_view():
    c = cam(h=20.0, heading=90.0, pitch=-45.0)
    pts, _ = c.pixel_to_ground(np.array([[INTR.cx, INTR.cy]]))
    assert pts[0] == pytest.approx([20.0, 0.0], abs=1e-6)


def test_nadir_image_top_points_along_heading():
    c = cam(h=20.0, heading=0.0)
    top, _ = c.pixel_to_ground(np.array([[INTR.cx, 0.0]]))
    assert top[0, 1] > 0  # top of image = north when heading north


def test_homography_roundtrip():
    c = cam(1.0, 2.0, 18.0, heading=30.0, pitch=-60.0, roll=1.5)
    world = np.array([[5.0, 10.0], [-3.0, 14.0], [8.0, 20.0]])
    H = c.ground_homography()
    uvw = np.column_stack([world, np.ones(3)]) @ H.T
    uv = uvw[:, :2] / uvw[:, 2:]
    back, hit = c.pixel_to_ground(uv)
    assert hit.all()
    assert back == pytest.approx(world, abs=1e-6)
    uv2, z = c.project(np.column_stack([world, np.zeros(3)]))
    assert uv2 == pytest.approx(uv, abs=1e-6)
    assert (z > 0).all()


def test_footprint_contains_nadir_point():
    c = cam(4.0, 4.0, 20.0)
    fp = np.array(c.footprint())
    assert fp[:, 0].min() < 4.0 < fp[:, 0].max()
    assert fp[:, 1].min() < 4.0 < fp[:, 1].max()


def test_heading_helpers():
    assert heading_from_vector(0, 1) == pytest.approx(0.0)
    assert heading_from_vector(1, 0) == pytest.approx(90.0)
    assert heading_from_vector(0, -1) == pytest.approx(180.0)
    assert wrap_deg(190) == pytest.approx(-170)
    assert wrap_deg(-180) == pytest.approx(180)
