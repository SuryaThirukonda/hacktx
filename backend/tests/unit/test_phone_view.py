import math

import numpy as np
import pytest

from overwatch.geometry.phone_view import (median_range, orientation_from_pose, rotate_to_upright, sensor_point,
                                           upright_hfov_deg, upright_point)


@pytest.mark.parametrize("orient", ["up", "down", "left", "right"])
def test_sensor_upright_round_trip(orient):
    for p in [(0.1, 0.2), (0.9, 0.05), (0.5, 0.5), (0.0, 1.0)]:
        s = sensor_point(p, orient)
        assert upright_point(s, orient) == pytest.approx(p)


@pytest.mark.parametrize("orient", ["up", "down", "left", "right"])
def test_rotate_to_upright_matches_point_mapping(orient):
    sensor = np.zeros((48, 64), np.uint8)  # landscape sensor image
    sensor[10, 50] = 255
    up = rotate_to_upright(sensor, orient)
    v, u = np.argwhere(up == 255)[0]
    H, W = up.shape
    sx, sy = sensor_point(((u + 0.5) / W, (v + 0.5) / H), orient)
    assert (int(sx * 64), int(sy * 48)) == (50, 10)


def test_orientation_from_pose_portrait_and_landscape():
    # ARKit pose columns are the camera axes in world (y up); portrait = image right points down
    portrait = np.eye(4)
    portrait[:3, 0] = [0, -1, 0]
    portrait[:3, 1] = [1, 0, 0]
    portrait[:3, 2] = [0, 0, 1]
    assert orientation_from_pose(portrait) == "right"
    assert orientation_from_pose(np.eye(4), previous="right") == "up"


def test_upright_hfov_uses_short_side_in_portrait():
    K = [[204.8, 0, 128.0], [0, 204.8, 96.0], [0, 0, 1]]  # 256x192 depth-resolution intrinsics, 64 deg wide
    land = upright_hfov_deg(K, "up", 640, 480, 64.0)
    port = upright_hfov_deg(K, "right", 480, 640, 64.0)
    assert land == pytest.approx(2 * math.degrees(math.atan(128 / 204.8)), abs=1e-6)
    assert port == pytest.approx(2 * math.degrees(math.atan(96 / 204.8)), abs=1e-6)
    assert port < land - 10
    # no intrinsics, older app without the orientation field: infer portrait from the JPEG aspect
    assert upright_hfov_deg(None, None, 480, 640, 64.0) == pytest.approx(port, abs=0.2)
    assert upright_hfov_deg(None, None, 640, 480, 64.0) == 64.0


def test_median_range_samples_the_rotated_region():
    depth = np.full((192, 256), 4.0, np.float32)
    conf = np.full(depth.shape, 2, np.uint8)
    # a person 1.5 m away occupies sensor columns 40-90 and rows 120-180 (portrait: upper-right of the upright image)
    depth[120:180, 40:90] = 1.5
    sx0, sy0, sx1, sy1 = 40 / 256, 120 / 192, 90 / 256, 180 / 192
    a, b = upright_point((sx0, sy0), "right"), upright_point((sx1, sy1), "right")
    box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
    assert median_range(box, "right", depth, conf) == pytest.approx(1.5)
    # ignoring the orientation reads the wrong part of the depth map
    assert median_range(box, "up", depth, conf) != pytest.approx(1.5)
