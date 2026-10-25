"""Camera-motion compensation (BoT-SORT style GMC): Shi-Tomasi corners outside detections, pyramidal LK flow,
robust homography. The homography maps previous-frame pixels to current-frame pixels."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CMCResult:
    H: np.ndarray  # 3x3, prev px -> current px
    ok: bool
    inliers: int
    shift_px: float
    reason: str = ""


class CameraMotionCompensator:
    def __init__(self, max_corners: int = 400, min_inliers: int = 20, downscale: int = 2) -> None:
        self.max_corners = max_corners
        self.min_inliers = min_inliers
        self.ds = downscale
        self.prev: np.ndarray | None = None
        self.prev_mask: np.ndarray | None = None

    def reset(self) -> None:
        self.prev = None

    def estimate(self, image: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> CMCResult:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        small = cv2.resize(gray, (gray.shape[1] // self.ds, gray.shape[0] // self.ds), interpolation=cv2.INTER_AREA)
        mask = np.full(small.shape, 255, np.uint8)
        for x0, y0, x1, y1 in boxes:
            pad = 4
            cv2.rectangle(mask, (int(x0 / self.ds) - pad, int(y0 / self.ds) - pad),
                          (int(x1 / self.ds) + pad, int(y1 / self.ds) + pad), 0, -1)
        prev, prev_mask = self.prev, self.prev_mask
        self.prev, self.prev_mask = small, mask
        ident = np.eye(3)
        if prev is None or prev.shape != small.shape:
            return CMCResult(ident, False, 0, 0.0, "no previous frame")
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=self.max_corners, qualityLevel=0.01, minDistance=6,
                                      mask=prev_mask, blockSize=5)
        if pts is None or len(pts) < self.min_inliers:
            return CMCResult(ident, False, 0, 0.0, "too few features")
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, small, pts, None, winSize=(15, 15), maxLevel=3)
        good = st.reshape(-1) == 1
        p0, p1 = pts.reshape(-1, 2)[good], nxt.reshape(-1, 2)[good]
        if len(p0) < self.min_inliers:
            return CMCResult(ident, False, int(len(p0)), 0.0, "flow lost")
        method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
        H, inl = cv2.findHomography(p0, p1, method, 1.5)
        if H is None or inl is None or int(inl.sum()) < self.min_inliers:
            return CMCResult(ident, False, int(0 if inl is None else inl.sum()), 0.0, "homography rejected")
        S = np.diag([self.ds, self.ds, 1.0])
        Hf = S @ H @ np.linalg.inv(S)
        Hf /= Hf[2, 2]
        c = np.array([image.shape[1] / 2, image.shape[0] / 2, 1.0])
        moved = Hf @ c
        shift = float(np.hypot(*(moved[:2] / moved[2] - c[:2])))
        return CMCResult(Hf, True, int(inl.sum()), shift)


def warp_point(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    p = H @ np.array([x, y, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def local_linear(H: np.ndarray, x: float, y: float) -> np.ndarray:
    """2x2 Jacobian of the homography at (x, y): how small displacements/sizes transform."""
    p = H @ np.array([x, y, 1.0])
    w = p[2]
    J = np.empty((2, 2))
    J[0, 0] = (H[0, 0] * w - p[0] * H[2, 0]) / w ** 2
    J[0, 1] = (H[0, 1] * w - p[0] * H[2, 1]) / w ** 2
    J[1, 0] = (H[1, 0] * w - p[1] * H[2, 0]) / w ** 2
    J[1, 1] = (H[1, 1] * w - p[1] * H[2, 1]) / w ** 2
    return J
