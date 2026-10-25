"""ArUco marker detection tuned for small aerial markers."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MarkerDetection:
    marker_id: int
    corners_px: np.ndarray  # (4, 2) TL, TR, BR, BL

    @property
    def center_px(self) -> tuple[float, float]:
        c = self.corners_px.mean(axis=0)
        return float(c[0]), float(c[1])

    @property
    def side_px(self) -> float:
        c = self.corners_px
        return float(np.mean([np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]))


class MarkerDetector:
    def __init__(self, dictionary: str = "DICT_4X4_50", known_ids: set[int] | None = None) -> None:
        d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
        p = cv2.aruco.DetectorParameters()
        p.adaptiveThreshWinSizeMin = 3
        p.adaptiveThreshWinSizeMax = 33
        p.adaptiveThreshWinSizeStep = 6
        p.minMarkerPerimeterRate = 0.012
        p.polygonalApproxAccuracyRate = 0.05
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        p.cornerRefinementWinSize = 3
        self.detector = cv2.aruco.ArucoDetector(d, p)
        self.known_ids = known_ids

    def detect(self, image: np.ndarray) -> list[MarkerDetection]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        corners, ids, _ = self.detector.detectMarkers(gray)
        out: list[MarkerDetection] = []
        if ids is None:
            return out
        for c, i in zip(corners, ids.reshape(-1)):
            mid = int(i)
            if self.known_ids is not None and mid not in self.known_ids:
                continue
            out.append(MarkerDetection(mid, c.reshape(4, 2).astype(float)))
        return out
