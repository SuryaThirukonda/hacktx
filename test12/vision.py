"""What the drone's camera pipeline finds in a frame.

* People: the pretrained COCO YOLO26n ('person'), either over the whole frame at 1280 px (searching) or on a 640 px
  crop around where the followed person is predicted to be (following); no training on simulator images.
* Red shirt: the share of saturated red pixels in the torso part of a person box (from the box's top 12 % to 60 %,
  without the outer 15 % each side). Measured on these frames: right 160 times out of 162.
* Police car: pretrained YOLO does not see the simulated patrol car as a car (it guesses skateboard, airplane or
  suitcase), so it is found by a classical colour detector instead, labelled as such: a red and a blue light-bar
  blob side by side, of similar size, on a white roof with black panels round it.
* Ground position: a box's bottom centre (the feet) through the camera the navigator believes in, onto the ground;
  a light bar onto the plane of its own height.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from overwatch.geometry.camera import PinholeCamera

from . import REPO_ROOT

RED_TORSO_MIN = 0.12
WEIGHTS = REPO_ROOT / "backend" / "weights" / "yolo26n.pt"


@dataclass
class PersonDet:
    box: tuple[float, float, float, float]
    conf: float
    red: float  # red share of the torso
    xy: tuple[float, float] | None = None  # ground position (world metres)

    @property
    def is_red(self) -> bool:
        return self.red >= RED_TORSO_MIN


@dataclass
class LightBarDet:
    box: tuple[float, float, float, float]  # around the red and blue blobs
    xy: tuple[float, float] | None = None
    score: float = 0.0


@dataclass
class FrameDetections:
    t: float
    mode: str  # "full" | "crop"
    people: list[PersonDet] = field(default_factory=list)
    bars: list[LightBarDet] = field(default_factory=list)
    ms: float = 0.0
    crop: tuple[int, int, int, int] | None = None


def torso_red(img: np.ndarray, box) -> float:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h, w = y1 - y0, x1 - x0
    crop = img[max(0, y0 + int(0.12 * h)):max(1, y0 + int(0.6 * h)), max(0, x0 + int(0.15 * w)):max(1, x1 - int(0.15 * w))]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = ((hsv[..., 0] <= 8) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 60)
    return float(red.mean())


def ground_xy(cam: PinholeCamera, uv: tuple[float, float], z: float = 0.0) -> tuple[float, float] | None:
    """Where the ray through pixel ``uv`` meets the horizontal plane at height ``z``."""
    ray = cam.pixel_rays_world(np.array([uv], float))[0]
    c = cam.pose.position
    if ray[2] > -1e-6:
        return None
    s = (z - c[2]) / ray[2]
    if s <= 0 or s > 400.0:
        return None
    return float(c[0] + s * ray[0]), float(c[1] + s * ray[1])


class PersonDetector:
    def __init__(self, threads: int | None = None) -> None:
        import torch
        from ultralytics import YOLO

        if threads:
            torch.set_num_threads(threads)
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:  # already set in this process
                pass
        self.model = YOLO(str(WEIGHTS))
        self.model_id = "YOLO26n (COCO, pretrained)"
        # the first inferences at each size are several times slower (graph setup): do them now, not mid-run
        for size in (1280, 640):
            self._predict(np.zeros((720 if size == 1280 else 640, size, 3), np.uint8), size)
        self.calls = 0
        self.total_ms = 0.0
        self.log: deque[tuple[float, str, int]] = deque(maxlen=300)  # (frame time, mode, ms) of recent passes

    def _predict(self, img: np.ndarray, imgsz: int) -> list[tuple[tuple[float, float, float, float], float]]:
        res = self.model.predict(img, imgsz=imgsz, conf=0.25, classes=[0], verbose=False)[0]
        if res.boxes is None:
            return []
        return [(tuple(float(v) for v in b), float(c)) for b, c in zip(res.boxes.xyxy.cpu().numpy(),
                                                                         res.boxes.conf.cpu().numpy())]

    def detect(self, img: np.ndarray, t: float, crop_centre: tuple[float, float] | None = None) -> FrameDetections:
        t0 = time.perf_counter()
        H, W = img.shape[:2]
        out = FrameDetections(t, "full" if crop_centre is None else "crop")
        if crop_centre is None:
            boxes = self._predict(img, 1280)
        else:
            s = 640
            x0 = int(np.clip(crop_centre[0] - s / 2, 0, W - s))
            y0 = int(np.clip(crop_centre[1] - s / 2, 0, H - s)) if H > s else 0
            crop = np.ascontiguousarray(img[y0:y0 + min(s, H), x0:x0 + s])
            boxes = [((b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0), c) for b, c in self._predict(crop, 640)]
            out.crop = (x0, y0, x0 + s, y0 + min(s, H))
        for b, c in boxes:
            out.people.append(PersonDet(b, c, torso_red(img, b)))
        out.ms = (time.perf_counter() - t0) * 1000.0
        self.calls += 1
        self.total_ms += out.ms
        self.log.append((round(t, 2), out.mode, round(out.ms)))
        return out


def find_light_bars(img: np.ndarray) -> list[LightBarDet]:
    """Red and blue light-bar halves side by side, the ring right round them mostly white (the roof they sit on), and
    black panels (hood, trunk, tyres) a little further out."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = (((h <= 8) | (h >= 172)) & (s >= 150) & (v >= 120)).astype(np.uint8)
    blue = ((h >= 100) & (h <= 128) & (s >= 150) & (v >= 120)).astype(np.uint8)
    if not red.any() or not blue.any():
        return []
    nr, lr, sr, cr = cv2.connectedComponentsWithStats(red, connectivity=8)
    nb, lb, sb, cb = cv2.connectedComponentsWithStats(blue, connectivity=8)
    reds = [i for i in range(1, nr) if 4 <= sr[i][4] <= 1500]
    blues = [i for i in range(1, nb) if 4 <= sb[i][4] <= 1500]
    out: list[LightBarDet] = []
    H, W = img.shape[:2]
    for i in reds:
        for j in blues:
            ra, ba = int(sr[i][4]), int(sb[j][4])
            size = math.sqrt(max(ra, ba))
            if math.hypot(cr[i][0] - cb[j][0], cr[i][1] - cb[j][1]) > 2.2 * size + 2 or not (0.33 <= ra / ba <= 3.0):
                continue
            x0, y0 = min(sr[i][0], sb[j][0]), min(sr[i][1], sb[j][1])
            x1 = max(sr[i][0] + sr[i][2], sb[j][0] + sb[j][2])
            y1 = max(sr[i][1] + sr[i][3], sb[j][1] + sb[j][3])
            pad = int(2.5 * size) + 4
            wx0, wy0, wx1, wy1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
            union = ((lr[wy0:wy1, wx0:wx1] == i) | (lb[wy0:wy1, wx0:wx1] == j)).astype(np.uint8)
            k = max(2, int(0.6 * size))
            grown = cv2.dilate(union, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
            ring = (grown > 0) & (cv2.dilate(union, np.ones((3, 3), np.uint8)) == 0)
            if not ring.any():
                continue
            ws, wv = s[wy0:wy1, wx0:wx1], v[wy0:wy1, wx0:wx1]
            white = float(((ws < 50) & (wv >= 180))[ring].mean())
            black = float((wv < 62).mean())
            if white >= 0.25 and black >= 0.06:
                out.append(LightBarDet((float(x0), float(y0), float(x1), float(y1)), score=round(min(white, 4 * black), 3)))
    return out
