"""Interchangeable object detectors.

* ``SimBlobDetector`` — classical HSV segmentation that runs on *pixels* of synthetic frames (people wear saturated
  clothing; grass, shadows, vehicles and crates are excluded by hue/saturation). It is a stand-in that is only
  meaningful on simulator imagery and is labelled as such (model_id ``sim-blob-v1``). It never reads ground truth.
* ``UltralyticsDetector`` — YOLO26 / YOLO11 checkpoints with an explicit ``name -> class`` map (no hard-coded ids).
* ``RFDETRDetector`` — Roboflow RF-DETR (optional dependency).
* ``TiledDetector`` — SAHI-style sliced inference around any of the above, for small overhead targets.
* ``NullDetector`` — honest "detector not running" placeholder.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    box: tuple[float, float, float, float]  # xyxy pixels
    confidence: float
    class_name: str = "person"
    extra: dict = field(default_factory=dict)

    @property
    def bottom_center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        return (0.5 * (x0 + x1), y1)


class Detector(Protocol):
    model_id: str
    valid_on: str  # "synthetic" | "real" | "any"

    def detect(self, image: np.ndarray) -> list[Detection]: ...


class NullDetector:
    model_id = "none"
    valid_on = "any"

    def detect(self, image: np.ndarray) -> list[Detection]:
        return []


class SimBlobDetector:
    model_id = "sim-blob-v1"
    valid_on = "synthetic"

    def __init__(self, min_area: int = 14, sat_min: int = 95, val_min: int = 45, miss_rate: float = 0.0,
                 seed: int = 0) -> None:
        self.min_area = min_area
        self.sat_min = sat_min
        self.val_min = val_min
        self.miss_rate = miss_rate
        self.rng = np.random.default_rng(seed)
        self._k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        self._k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._k_grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    def detect(self, image: np.ndarray) -> list[Detection]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        green = (h >= 30) & (h <= 95)
        sat = (s >= self.sat_min) & (v >= self.val_min) & ~green
        sat = sat.astype(np.uint8) * 255
        sat = cv2.morphologyEx(sat, cv2.MORPH_OPEN, self._k_open)
        # extend saturated clothing blobs with adjacent dark, unsaturated pixels (dark trousers, shoes, hair)
        dark = ((v < 82) & (s < 90)).astype(np.uint8) * 255
        light = ((v > 165) & (s < 70)).astype(np.uint8) * 255  # reflective stripes, white garments
        near = cv2.dilate(sat, self._k_grow)
        person = cv2.bitwise_or(sat, cv2.bitwise_and(cv2.bitwise_or(dark, light), near))
        person = cv2.morphologyEx(person, cv2.MORPH_CLOSE, self._k_close)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(person, connectivity=8)
        out: list[Detection] = []
        H, W = image.shape[:2]
        for i in range(1, n):
            x, y, w, hh, area = stats[i]
            if area < self.min_area or w < 3 or hh < 3:
                continue
            if y == 0 and w > 0.6 * W:
                continue  # an image-wide region hanging from the top edge (sky, canopy) is not a person
            aspect = hh / max(w, 1)
            fill = area / float(w * hh)
            if not (0.2 <= aspect <= 5.0) or fill < 0.22:
                continue
            core = labels[y:y + hh, x:x + w] == i
            sat_frac = float((sat[y:y + hh, x:x + w][core] > 0).mean())
            if sat_frac < 0.25:
                continue  # mostly dark pixels: not a clothed person blob
            mean_s = float(s[y:y + hh, x:x + w][core].mean()) / 255.0
            size_term = min(1.0, area / 260.0)
            conf = float(np.clip(0.28 + 0.42 * size_term + 0.35 * (mean_s - 0.35) + 0.15 * fill, 0.12, 0.97))
            if self.miss_rate > 0 and self.rng.random() < self.miss_rate:
                continue
            out.append(Detection((float(x), float(y), float(min(W, x + w)), float(min(H, y + hh))), conf, "person",
                                 {"area": int(area), "sat_frac": round(sat_frac, 2)}))
        return self._merge_parts(out)

    @staticmethod
    def _merge_parts(dets: list[Detection]) -> list[Detection]:
        """Merge vertically stacked fragments of one person (head blob above a torso blob, split vests)."""
        dets = sorted(dets, key=lambda d: d.box[1])
        merged = True
        while merged:
            merged = False
            for i in range(len(dets)):
                for j in range(i + 1, len(dets)):
                    a, b = dets[i].box, dets[j].box
                    wa, wb = a[2] - a[0], b[2] - b[0]
                    ha, hb = a[3] - a[1], b[3] - b[1]
                    xov = min(a[2], b[2]) - max(a[0], b[0])
                    gap = max(a[1], b[1]) - min(a[3], b[3])
                    small, big = (dets[i], dets[j]) if wa * ha < wb * hb else (dets[j], dets[i])
                    sb, bb = small.box, big.box
                    small_area = (sb[2] - sb[0]) * (sb[3] - sb[1])
                    big_area = (bb[2] - bb[0]) * (bb[3] - bb[1])
                    scx, scy = (sb[0] + sb[2]) / 2, (sb[1] + sb[3]) / 2
                    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
                    inside = (bb[0] - 0.35 * bw <= scx <= bb[2] + 0.35 * bw and bb[1] - 0.6 * bh <= scy <= bb[3] + 0.6 * bh)
                    stacked = xov > 0.3 * min(wa, wb) and gap < max(4.0, 0.6 * max(ha, hb))
                    if (stacked or inside) and small_area < 0.6 * big_area:
                        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                        conf = max(dets[i].confidence, dets[j].confidence)
                        extra = {"area": dets[i].extra.get("area", 0) + dets[j].extra.get("area", 0), "merged": True}
                        dets[i] = Detection(box, conf, "person", extra)
                        del dets[j]
                        merged = True
                        break
                if merged:
                    break
        return dets


class UltralyticsDetector:
    valid_on = "real"
    FALLBACK_WEIGHTS = ("yolo26n.pt", "yolo11n.pt", "yolov8n.pt")

    def __init__(self, weights: str = "yolo26n.pt", imgsz: int = 960, conf: float = 0.25,
                 classes: list[str] | None = None, device: str = "auto") -> None:
        from ultralytics import YOLO  # optional dependency

        last_err: Exception | None = None
        from ..config import REPO_ROOT

        local = REPO_ROOT / "backend" / "weights"
        tried = [weights] + [w for w in self.FALLBACK_WEIGHTS if w != weights]
        tried = [str(local / w) if (local / w).exists() else w for w in tried]
        self.model = None
        for w in tried:
            try:
                self.model = YOLO(w)
                self.weights = Path(w).name
                break
            except Exception as e:  # noqa: BLE001 - try next checkpoint
                last_err = e
                log.warning("could not load %s: %s", w, e)
        if self.model is None:
            raise RuntimeError(f"no YOLO weights could be loaded ({tried}): {last_err}")
        names: dict[int, str] = self.model.names if isinstance(self.model.names, dict) else dict(enumerate(self.model.names))
        wanted = set(classes or ["person"])
        self.class_map = {i: n for i, n in names.items() if n in wanted}
        missing = wanted - set(self.class_map.values())
        if missing:
            log.warning("checkpoint %s has no classes %s", self.weights, sorted(missing))
        self.imgsz = imgsz
        self.conf = conf
        self.device = _resolve_device(device)
        self.model_id = f"ultralytics:{self.weights}@{imgsz}:{self.device}"
        self.last_ms = 0.0

    def detect(self, image: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        res = self.model.predict(image, imgsz=self.imgsz, conf=self.conf, classes=list(self.class_map),
                                 device=self.device, verbose=False)[0]
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        out: list[Detection] = []
        if res.boxes is None:
            return out
        xyxy = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        clss = res.boxes.cls.cpu().numpy().astype(int)
        for b, c, k in zip(xyxy, confs, clss):
            name = self.class_map.get(int(k))
            if name is None:
                continue
            out.append(Detection((float(b[0]), float(b[1]), float(b[2]), float(b[3])), float(c), name))
        return out


class RFDETRDetector:
    valid_on = "real"

    def __init__(self, size: str = "small", conf: float = 0.35, classes: list[str] | None = None) -> None:
        import rfdetr  # optional dependency
        from rfdetr.util.coco_classes import COCO_CLASSES  # type: ignore[import-not-found]

        cls = {"nano": "RFDETRNano", "small": "RFDETRSmall", "medium": "RFDETRMedium"}.get(size, "RFDETRSmall")
        self.model = getattr(rfdetr, cls)()
        self.names = COCO_CLASSES
        self.wanted = set(classes or ["person"])
        self.conf = conf
        self.model_id = f"rfdetr:{size}"

    def detect(self, image: np.ndarray) -> list[Detection]:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        dets = self.model.predict(rgb, threshold=self.conf)
        out = []
        for b, c, k in zip(dets.xyxy, dets.confidence, dets.class_id):
            name = self.names.get(int(k)) if isinstance(self.names, dict) else self.names[int(k)]
            if name in self.wanted:
                out.append(Detection((float(b[0]), float(b[1]), float(b[2]), float(b[3])), float(c), name))
        return out


class TiledDetector:
    """Sliced inference (SAHI-style) for people only a few pixels tall: the wrapped detector runs on overlapping
    native-resolution tiles (and optionally the whole frame), boxes are mapped back, and duplicates are merged.
    A box cut by an interior tile seam only survives when no complete box covers it."""

    def __init__(self, base: Detector, tile_px: int = 640, overlap: float = 0.2, full_frame: bool = True,
                 iou: float = 0.5, ios: float = 0.6) -> None:
        self.base = base
        self.tile_px = int(tile_px)
        self.overlap = float(min(max(overlap, 0.0), 0.9))
        self.full_frame = full_frame
        self.iou = iou
        self.ios = ios
        self.valid_on = base.valid_on
        self.model_id = f"{base.model_id}+tiles{self.tile_px}"
        self.last_ms = 0.0
        self.last_tiles = 0

    @staticmethod
    def _starts(n: int, tile: int, step: int) -> list[int]:
        """Evenly spread tile origins with at least the requested overlap."""
        if n <= tile:
            return [0]
        k = math.ceil((n - tile) / step) + 1
        return [round(i * (n - tile) / (k - 1)) for i in range(k)]

    def tiles(self, W: int, H: int) -> list[tuple[int, int, int, int]]:
        step = max(1, int(self.tile_px * (1.0 - self.overlap)))
        return [(x, y, min(W, x + self.tile_px), min(H, y + self.tile_px))
                for y in self._starts(H, self.tile_px, step) for x in self._starts(W, self.tile_px, step)]

    def detect(self, image: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        H, W = image.shape[:2]
        tiles = self.tiles(W, H)
        pool: list[tuple[bool, Detection]] = []
        for x0, y0, x1, y1 in tiles:
            for d in self.base.detect(np.ascontiguousarray(image[y0:y1, x0:x1])):
                b = (d.box[0] + x0, d.box[1] + y0, d.box[2] + x0, d.box[3] + y0)
                cut = ((x0 > 0 and b[0] <= x0 + 2) or (x1 < W and b[2] >= x1 - 2) or
                       (y0 > 0 and b[1] <= y0 + 2) or (y1 < H and b[3] >= y1 - 2))
                pool.append((cut, Detection(b, d.confidence, d.class_name, {**d.extra, "tile": (x0, y0)})))
        if self.full_frame and len(tiles) > 1:
            pool += [(False, d) for d in self.base.detect(image)]
        pool.sort(key=lambda cd: (cd[0], -cd[1].confidence))  # complete boxes first, then by confidence
        kept: list[Detection] = []
        for _, d in pool:
            if all(k.class_name != d.class_name or (_iou(k.box, d.box) < self.iou and _ios(k.box, d.box) < self.ios)
                   for k in kept):
                kept.append(d)
        self.last_tiles = len(tiles)
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return kept


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _ios(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Intersection over the smaller box."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def build_detector(name: str, provenance: str, cfg) -> Detector:
    """Pick a detector. ``auto`` uses the sim detector only on synthetic frames, YOLO on real video."""
    pc = cfg.perception
    choice = name
    if name == "auto":
        choice = "sim_blob" if provenance == "SYNTHETIC" else "ultralytics"
    try:
        det: Detector | None = None
        if choice == "sim_blob":
            det = SimBlobDetector()
        elif choice == "ultralytics":
            det = UltralyticsDetector(pc.yolo_weights, pc.yolo_imgsz, pc.confidence_min, pc.classes, pc.device)
        elif choice == "rfdetr":
            det = RFDETRDetector(conf=pc.confidence_min, classes=pc.classes)
        if det is not None:
            tl = pc.tiling
            return TiledDetector(det, tl.tile_px, tl.overlap, tl.full_frame) if tl.enabled else det
    except Exception as e:  # noqa: BLE001 - honest fallback, surfaced in health
        log.error("detector %s unavailable: %s", choice, e)
        d = NullDetector()
        d.model_id = f"none ({choice} unavailable: {type(e).__name__})"
        return d
    return NullDetector()
