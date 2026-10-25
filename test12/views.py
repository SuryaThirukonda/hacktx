"""The two simulator cameras besides the drone's own, for the person watching: a third-person chase camera a few
metres behind and above the drone, and a bird's-eye camera straight above it, north up like the map. The drone is
drawn in both (to scale); a small live copy of the drone's camera sits in their corner. Nothing the drone decides
uses them: they are simulator views, and their overlay says so.

Both follow the drone smoothly (the chase camera's heading lags the drone's by about 0.8 s, the bird's-eye camera's
position by 0.6 s), and both are updated once per physics step, so a seed replays the same pictures.
"""

from __future__ import annotations

import math

import numpy as np

from overwatch.geometry.camera import CameraPose, Intrinsics, PinholeCamera, wrap_deg

from .drone import CAMERA, Mini2

VIEWS = ("drone", "chase", "top")
VIEW_NAMES = {"drone": "Drone camera", "chase": "Third-person view", "top": "Bird's-eye view"}
CHASE = Intrinsics.from_hfov(1280, 720, 75.0)
TOP = Intrinsics.from_hfov(1280, 720, 60.0)
INSET = Intrinsics.from_hfov(384, 216, 71.6)  # the drone camera, small, in the corner of the other two
INTRINSICS = {"drone": CAMERA, "chase": CHASE, "top": TOP, "inset": INSET}
CHASE_BACK, CHASE_UP = 3.2, 1.3  # metres behind and above the drone: it is about 70 px across in the picture
TOP_HEIGHT = 95.0  # metres above the ground: 110 x 62 m of it in view


class ViewCams:
    def __init__(self, drone: Mini2, box_tops: np.ndarray) -> None:
        self.box_tops = box_tops  # (n, 5): x0, x1, y0, y1 and top of every box on the map
        self.yaw = drone.heading
        self.pitch = -25.0
        self.top_xy = drone.pos[:2].copy()

    def update(self, dt: float, d: Mini2) -> None:
        self.yaw = (self.yaw + wrap_deg(d.heading - self.yaw) * min(1.0, dt / 0.8)) % 360.0
        self.pitch += (self._want_pitch(d) - self.pitch) * min(1.0, dt / 0.5)
        self.top_xy = self.top_xy + (d.pos[:2] - self.top_xy) * min(1.0, dt / 0.6)

    def _chase_pos(self, d: Mini2) -> np.ndarray:
        h = math.radians(self.yaw)
        p = np.array([d.pos[0] - math.sin(h) * CHASE_BACK, d.pos[1] - math.cos(h) * CHASE_BACK, d.pos[2] + CHASE_UP])
        B = self.box_tops  # on the pad the drone is low among buildings: never put the camera inside one
        under = (B[:, 0] - 1.0 <= p[0]) & (p[0] <= B[:, 1] + 1.0) & (B[:, 2] - 1.0 <= p[1]) & (p[1] <= B[:, 3] + 1.0)
        if under.any():
            p[2] = max(p[2], float(B[under, 4].max()) + 2.0)
        p[2] = max(p[2], 2.0)
        return p

    def _want_pitch(self, d: Mini2) -> float:
        """Down far enough to show the ground the drone's camera is looking at, not so far that the drone leaves the
        top of the picture."""
        p = self._chase_pos(d)
        h = math.radians(d.heading)
        dep = math.radians(-d.gimbal)
        reach = min(d.pos[2] / math.tan(dep), 60.0) if dep > math.radians(5.0) else 60.0
        aim = d.pos[:2] + np.array([math.sin(h), math.cos(h)]) * reach
        dep_aim = math.degrees(math.atan2(p[2], max(float(np.linalg.norm(aim - p[:2])), 0.1)))
        dep_drone = math.degrees(math.atan2(p[2] - d.pos[2], max(float(np.linalg.norm(d.pos[:2] - p[:2])), 0.1)))
        half = math.degrees(math.atan(CHASE.cy / CHASE.fy))
        want = min((dep_aim + dep_drone) / 2, dep_drone + half - 9.0)  # the drone well inside the top edge
        return -float(np.clip(want, 8.0, 80.0))

    def pose(self, view: str, d: Mini2) -> tuple[list[float], float, float]:
        if view == "chase":
            return self._chase_pos(d).tolist(), float(self.yaw), float(self.pitch)
        return [float(self.top_xy[0]), float(self.top_xy[1]), TOP_HEIGHT], 0.0, -90.0

    def camera(self, view: str, d: Mini2) -> PinholeCamera:
        pos, heading, pitch = self.pose(view, d)
        return PinholeCamera(INTRINSICS[view], CameraPose(np.array(pos, float), heading, pitch))
