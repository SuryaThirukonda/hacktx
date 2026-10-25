"""Pinhole camera model in the local ENU frame (x east, y north, z up; meters).

Conventions (unit-tested in tests/unit/test_camera.py):
* heading: degrees clockwise from local +y ("north"), like DJI aircraft yaw;
* pitch: degrees, negative looks down (gimbal -90 = nadir);
* camera axes follow OpenCV: x right, y down, z forward; ``R`` maps world vectors into the camera frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @classmethod
    def from_hfov(cls, width: int, height: int, hfov_deg: float) -> Intrinsics:
        fx = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        return cls(fx=fx, fy=fx, cx=width / 2.0, cy=height / 2.0, width=width, height=height)

    @property
    def K(self) -> np.ndarray:
        return np.array([[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]])

    @property
    def K_inv(self) -> np.ndarray:
        return np.linalg.inv(self.K)

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.width / 2.0 / self.fx))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.height / 2.0 / self.fy))

    def scaled(self, width: int, height: int) -> Intrinsics:
        sx, sy = width / self.width, height / self.height
        return Intrinsics(self.fx * sx, self.fy * sy, self.cx * sx, self.cy * sy, width, height)


def rotation_world_to_cam(heading_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    h, p, r = math.radians(heading_deg), math.radians(pitch_deg), math.radians(roll_deg)
    forward = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    down = np.cross(forward, right)
    if r != 0.0:
        right, down = math.cos(r) * right + math.sin(r) * down, -math.sin(r) * right + math.cos(r) * down
    return np.stack([right, down, forward])


@dataclass
class CameraPose:
    position: np.ndarray  # (3,) world ENU, meters
    heading_deg: float
    pitch_deg: float
    roll_deg: float = 0.0

    def copy(self) -> CameraPose:
        return CameraPose(self.position.copy(), self.heading_deg, self.pitch_deg, self.roll_deg)


@dataclass
class PinholeCamera:
    intr: Intrinsics
    pose: CameraPose
    R: np.ndarray = field(init=False)
    t: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.R = rotation_world_to_cam(self.pose.heading_deg, self.pose.pitch_deg, self.pose.roll_deg)
        self.t = -self.R @ np.asarray(self.pose.position, dtype=float)

    # -- projection -----------------------------------------------------------------------------------------
    def world_to_cam(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        return pts @ self.R.T + self.t

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Project Nx3 world points -> (Nx2 pixel coords, N depths along the optical axis)."""
        pc = self.world_to_cam(pts)
        z = pc[:, 2]
        zs = np.where(np.abs(z) < 1e-9, 1e-9, z)
        u = self.intr.fx * pc[:, 0] / zs + self.intr.cx
        v = self.intr.fy * pc[:, 1] / zs + self.intr.cy
        return np.stack([u, v], axis=1), z

    def ground_homography(self) -> np.ndarray:
        """H such that [u v 1]^T ~ H [X Y 1]^T for world points on the ground plane z = 0."""
        H = self.intr.K @ np.column_stack([self.R[:, 0], self.R[:, 1], self.t])
        return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H

    def image_to_ground_homography(self) -> np.ndarray:
        H = np.linalg.inv(self.ground_homography())
        return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H

    # -- rays -----------------------------------------------------------------------------------------------
    def pixel_rays_world(self, uv: np.ndarray) -> np.ndarray:
        uv = np.asarray(uv, dtype=float).reshape(-1, 2)
        ones = np.ones((uv.shape[0], 1))
        d_cam = np.hstack([uv, ones]) @ self.intr.K_inv.T
        return d_cam @ self.R  # == (R^T d_cam^T)^T

    def pixel_to_ground(self, uv: np.ndarray, max_range_m: float = 500.0) -> tuple[np.ndarray, np.ndarray]:
        """Intersect pixel rays with z=0. Returns (Nx2 ground xy, N bool hit mask)."""
        rays = self.pixel_rays_world(uv)
        cz = float(self.pose.position[2])
        rz = rays[:, 2]
        hit = rz < -1e-6
        s = np.where(hit, -cz / np.where(hit, rz, -1.0), np.nan)
        pts = self.pose.position[None, :2] + s[:, None] * rays[:, :2]
        dist = np.hypot(pts[:, 0] - self.pose.position[0], pts[:, 1] - self.pose.position[1])
        hit &= dist <= max_range_m
        return pts, hit

    def ray_z_coefficients(self) -> np.ndarray:
        """(a, b, c) with world-ray z component = a*u + b*v + c (unnormalised rays); used for horizon masks."""
        M = self.R.T @ self.intr.K_inv
        return M[2, :]

    def footprint(self, max_range_m: float = 120.0) -> list[tuple[float, float]] | None:
        """Ground polygon seen by the camera (image corners projected), clipped to max range."""
        w, h = self.intr.width, self.intr.height
        corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=float)
        edge_pts: list[np.ndarray] = []
        # sample along the border so polygons stay correct when the top edge is near the horizon
        for (u0, v0), (u1, v1) in zip(corners, np.roll(corners, -1, axis=0)):
            for s in np.linspace(0, 1, 6, endpoint=False):
                edge_pts.append(np.array([u0 + (u1 - u0) * s, v0 + (v1 - v0) * s]))
        uv = np.array(edge_pts)
        rays = self.pixel_rays_world(uv)
        cz = float(self.pose.position[2])
        out = []
        for ray in rays:
            horiz = math.hypot(ray[0], ray[1])
            if ray[2] < -1e-6:
                s = -cz / ray[2]
                d = s * horiz
                if d > max_range_m:
                    s = max_range_m / max(horiz, 1e-9)
            else:
                s = max_range_m / max(horiz, 1e-9)
            out.append((float(self.pose.position[0] + s * ray[0]), float(self.pose.position[1] + s * ray[1])))
        return out if len(out) >= 3 else None

    def ground_sample_distance(self) -> float | None:
        """Meters per pixel at the image centre on the ground (None if the centre ray misses the ground)."""
        c = np.array([[self.intr.cx, self.intr.cy], [self.intr.cx + 1, self.intr.cy]])
        pts, hit = self.pixel_to_ground(c)
        if not hit.all():
            return None
        return float(np.hypot(*(pts[1] - pts[0])))


def heading_from_vector(dx: float, dy: float) -> float:
    """Heading (deg clockwise from +y) of a local-frame vector."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def wrap_deg(a: float) -> float:
    """Wrap to (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a
