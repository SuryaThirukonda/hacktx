"""The aircraft: a DJI Mini 2 stand-in and its dead-reckoning navigator.

Flight model: first-order response to virtual-stick commands (forward, right, up, yaw rate), the same time constants
and caps as the repo's Mini 2 profile (``overwatch/sim/aircraft.py``: tau 0.55 s horizontal, 0.6 s vertical, 0.3 s
yaw; 15 m/s, 4 m/s and 100 deg/s Overwatch caps), and the Mini 2's pitch-only gimbal (-90 to 0 deg at 60 deg/s); the
aircraft yaws to pan. Perfect conditions, as agreed for this test: no wind, no sensor noise.

Navigation without GPS: the navigator starts at the pad (0, 0) and adds up the flight controller's velocity and the
compass heading every step (how far it travelled); altitude comes from the barometer, the camera's direction from the
heading and the gimbal angle. It never reads the true position. With perfect sensors its estimate equals the truth,
which the page shows as the dead-reckoning error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from overwatch.geometry.camera import CameraPose, Intrinsics, PinholeCamera

TAU_V, TAU_VZ, TAU_YAW = 0.55, 0.6, 0.3
MAX_H, MAX_UP, MAX_DOWN, MAX_YAW = 15.0, 4.0, 3.0, 100.0
GIMBAL_RATE, GIMBAL_MIN, GIMBAL_MAX = 60.0, -90.0, 0.0
CAMERA = Intrinsics.from_hfov(1280, 720, 71.6)  # the Mini 2's 720p live view


@dataclass
class Stick:
    forward: float = 0.0
    right: float = 0.0
    up: float = 0.0
    yaw_rate: float = 0.0


class Mini2:
    def __init__(self, x: float, y: float, heading: float) -> None:
        self.pos = np.array([x, y, 0.0])
        self.vel = np.zeros(3)
        self.heading = heading
        self.yaw_rate = 0.0
        self.gimbal = -90.0
        self.gimbal_target = -90.0
        self.flying = False
        self.distance_m = 0.0

    def step(self, dt: float, cmd: Stick, gimbal_target: float | None = None) -> None:
        h = math.radians(self.heading)
        fwd = np.array([math.sin(h), math.cos(h)])
        right = np.array([math.cos(h), -math.sin(h)])
        f = float(np.clip(cmd.forward, -MAX_H, MAX_H))
        r = float(np.clip(cmd.right, -MAX_H, MAX_H))
        v_goal = fwd * f + right * r
        sp = float(np.linalg.norm(v_goal))
        if sp > MAX_H:
            v_goal *= MAX_H / sp
        vz_goal = float(np.clip(cmd.up, -MAX_DOWN, MAX_UP))
        yaw_goal = float(np.clip(cmd.yaw_rate, -MAX_YAW, MAX_YAW))
        if not self.flying and vz_goal <= 0.0:
            v_goal[:] = 0.0
            vz_goal = 0.0
            yaw_goal = 0.0
        self.vel[:2] += (v_goal - self.vel[:2]) * min(1.0, dt / TAU_V)
        self.vel[2] += (vz_goal - self.vel[2]) * min(1.0, dt / TAU_VZ)
        self.yaw_rate += (yaw_goal - self.yaw_rate) * min(1.0, dt / TAU_YAW)
        step = self.vel * dt
        self.pos += step
        if self.pos[2] <= 0.0:
            self.pos[2] = 0.0
            self.vel[2] = max(0.0, self.vel[2])
        self.flying = self.pos[2] > 0.2
        self.distance_m += float(np.linalg.norm(step))
        self.heading = (self.heading + self.yaw_rate * dt) % 360.0
        if gimbal_target is not None:
            self.gimbal_target = float(np.clip(gimbal_target, GIMBAL_MIN, GIMBAL_MAX))
        self.gimbal += float(np.clip(self.gimbal_target - self.gimbal, -GIMBAL_RATE * dt, GIMBAL_RATE * dt))

    # the flight controller's own outputs, which is all the navigator sees
    def imu_velocity(self) -> np.ndarray:
        return self.vel.copy()

    def compass_deg(self) -> float:
        return self.heading

    def baro_alt(self) -> float:
        return float(self.pos[2])


class DeadReckoning:
    """Position relative to the pad from the flight controller's velocity, integrated every step (no GPS)."""

    def __init__(self) -> None:
        self.xy = np.zeros(2)  # base frame: metres east and north of the pad
        self.alt = 0.0
        self.heading = 0.0
        self.gimbal = -90.0

    def update(self, dt: float, vel: np.ndarray, heading: float, baro_alt: float, gimbal: float) -> None:
        self.xy = self.xy + vel[:2] * dt
        self.alt = baro_alt
        self.heading = heading
        self.gimbal = gimbal

    def camera(self, pad_xy) -> PinholeCamera:
        """The camera as the navigator believes it is: where the target pixels are projected from."""
        p = np.array([pad_xy[0] + self.xy[0], pad_xy[1] + self.xy[1], self.alt])
        return PinholeCamera(CAMERA, CameraPose(p, self.heading, self.gimbal))


def true_camera(d: Mini2) -> PinholeCamera:
    return PinholeCamera(CAMERA, CameraPose(d.pos.copy(), d.heading, d.gimbal))
