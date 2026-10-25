"""Session-scoped appearance features for subject re-acquisition (no faces, no biometrics).

A colour histogram of the torso region is enough to separate a red shirt from a blue one; it is NOT an identity.
Galleries live only inside a session and are discarded at session end.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

import cv2
import numpy as np

HIST_BINS = (12, 4)


def torso_region(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    h = y1 - y0
    w = x1 - x0
    return int(x0 + 0.15 * w), int(y0 + 0.12 * h), int(x1 - 0.15 * w), int(y0 + 0.62 * h)


def color_hist(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    H, W = image.shape[:2]
    x0, y0, x1, y1 = torso_region(box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        x0, y0, x1, y1 = (int(max(0, box[0])), int(max(0, box[1])), int(min(W, box[2])), int(min(H, box[3])))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
    crop = image[y0:y1, x0:x1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 50, 35), (180, 255, 255))  # ignore grey/dark background pixels
    # aerial crops are dominated by grass/canopy background: drop green hues from the descriptor
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(cv2.inRange(hsv, (33, 40, 20), (90, 255, 255))))
    if cv2.countNonZero(mask) < 4:
        mask = None
    hist = cv2.calcHist([hsv], [0, 1], mask, list(HIST_BINS), [0, 180, 0, 256])
    hist = hist.flatten().astype(np.float32)
    s = hist.sum()
    return hist / s if s > 0 else None


def similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 0.0
    d = cv2.compareHist(a.astype(np.float32), b.astype(np.float32), cv2.HISTCMP_BHATTACHARYYA)
    return float(np.clip(1.0 - d, 0.0, 1.0))


def crop_tile(image: np.ndarray, reference: np.ndarray | None, boxes: list[tuple[float, float, float, float]],
              min_side: int = 280, cell: int = 160) -> tuple[np.ndarray, list[str]]:
    """Reference crop (if any) + labelled candidate crops in one image for D2/D3 decision questions.

    Each cell is ``cell`` px; the tile is padded so both sides are >= ``min_side`` (TypeSafe requires 280-1568 px).
    """
    labels = list(string.ascii_uppercase[: len(boxes)])
    cells: list[np.ndarray] = []
    names: list[str] = []
    if reference is not None:
        cells.append(_fit(reference, cell))
        names.append("REF")
    H, W = image.shape[:2]
    for lab, (x0, y0, x1, y1) in zip(labels, boxes):
        pad = 0.25 * max(x1 - x0, y1 - y0)
        cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
        cx1, cy1 = int(min(W, x1 + pad)), int(min(H, y1 + pad))
        crop = image[cy0:cy1, cx0:cx1] if cx1 > cx0 and cy1 > cy0 else np.zeros((8, 8, 3), np.uint8)
        cells.append(_fit(crop, cell))
        names.append(lab)
    tile = np.hstack(cells) if cells else np.zeros((cell, cell, 3), np.uint8)
    for i, name in enumerate(names):
        cv2.rectangle(tile, (i * cell, 0), (i * cell + 46, 24), (0, 0, 0), -1)
        cv2.putText(tile, name, (i * cell + 4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    th, tw = tile.shape[:2]
    if th < min_side or tw < min_side:
        canvas = np.zeros((max(th, min_side), max(tw, min_side), 3), np.uint8)
        canvas[:th, :tw] = tile
        tile = canvas
    return tile, labels


def _fit(img: np.ndarray, cell: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = cell / max(h, w, 1)
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_CUBIC)
    out = np.zeros((cell, cell, 3), np.uint8)
    out[(cell - r.shape[0]) // 2:(cell - r.shape[0]) // 2 + r.shape[0], (cell - r.shape[1]) // 2:(cell - r.shape[1]) // 2 + r.shape[1]] = r
    return out


COLOR_NAMES = [
    (0, 8, "red"), (8, 20, "orange"), (20, 33, "yellow"), (33, 78, "green"), (78, 98, "cyan"),
    (98, 128, "blue"), (128, 150, "purple"), (150, 170, "pink"), (170, 181, "red"),
]


COLOR_WORDS = {"red": "red", "maroon": "red", "crimson": "red", "orange": "orange", "yellow": "yellow",
               "mustard": "yellow", "green": "green", "olive": "green", "cyan": "cyan", "teal": "cyan", "blue": "blue",
               "navy": "blue", "denim": "blue", "purple": "purple", "violet": "purple", "pink": "pink", "black": "black",
               "dark": "black", "white": "white", "gray": "gray", "grey": "gray", "silver": "gray"}
TORSO_WORDS = {"shirt", "tshirt", "t-shirt", "tee", "top", "hoodie", "jacket", "sweater", "sweatshirt", "coat", "vest",
               "blouse", "jersey"}
LOWER_WORDS = {"pants", "trousers", "jeans", "shorts", "skirt", "leggings", "joggers", "slacks"}
# hue ranges (OpenCV 0-180) of the chromatic colour words, for scoring a torso histogram against a word
_HUES = {name: [(a, b) for a, b, n in COLOR_NAMES if n == name] for _, _, name in COLOR_NAMES}


@dataclass(frozen=True)
class ClothingDescriptor:
    """What an operator said about someone's clothing, as colour words per garment (never a face or a name)."""

    torso: str | None
    lower: str | None
    words: tuple[str, ...] = ()

    def text(self) -> str:
        parts = [f"{self.torso} top" if self.torso else "", f"{self.lower} lower" if self.lower else ""]
        return ", ".join(p for p in parts if p) or "no clothing words"


def parse_description(text: str) -> ClothingDescriptor | None:
    """"look for red shirt, jeans" -> torso red, lower blue. A colour before a garment word goes to that garment;
    "jeans" alone means blue trousers; a lone colour is the top. None when there are no clothing words."""
    words = [w.strip(".,;:!?\"'()").lower() for w in text.replace(",", " , ").split()]
    torso = lower = None
    pending: str | None = None
    for w in words:
        if w in COLOR_WORDS:
            pending = COLOR_WORDS[w]
        elif w in TORSO_WORDS:
            torso, pending = pending or torso, None
        elif w in LOWER_WORDS:
            lower = pending or lower or ("blue" if w == "jeans" else None)
            pending = None
        elif w == ",":
            if pending and torso is None:
                torso = pending
            pending = None
    if pending:
        if torso is None:
            torso = pending
        elif lower is None:
            lower = pending
    if torso is None and lower is None:
        return None
    return ClothingDescriptor(torso, lower, tuple(w for w in words if w in COLOR_WORDS or w in TORSO_WORDS | LOWER_WORDS))


def _hue_fraction(hist: np.ndarray, color: str) -> float | None:
    """Share of a torso histogram (saturated pixels only) in a chromatic colour's hue range."""
    rng = _HUES.get(color)
    if not rng:
        return None
    h = hist.reshape(HIST_BINS)
    bin_w = 180.0 / HIST_BINS[0]
    frac = 0.0
    for i in range(HIST_BINS[0]):
        lo, hi = i * bin_w, (i + 1) * bin_w
        overlap = sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in rng) / bin_w
        frac += overlap * float(h[i].sum())
    return float(min(1.0, frac))


def descriptor_likelihood(desc: ClothingDescriptor | None, hist: np.ndarray | None, colors: tuple[str, str] | None,
                          match: float = 0.85, mismatch: float = 0.12, unknown: float = 0.5) -> float:
    """P(this description | this person's clothing): per described garment, a match, a mismatch, or no information
    (colour unknown). The torso uses the session histogram when the colour is chromatic, else the colour word."""
    if desc is None:
        return 1.0
    p = 1.0
    if desc.torso is not None:
        frac = _hue_fraction(hist, desc.torso) if hist is not None else None
        if frac is not None:
            p *= mismatch + (match - mismatch) * min(1.0, frac / 0.6)
        elif colors and colors[0] not in ("unknown", None):
            p *= match if colors[0] == desc.torso else mismatch
        else:
            p *= unknown
    if desc.lower is not None:
        if colors and colors[1] not in ("unknown", None):
            p *= match if colors[1] == desc.lower else mismatch
        else:
            p *= unknown
    return float(max(p, 1e-3))


def dominant_color_name(image: np.ndarray, box: tuple[float, float, float, float], region: str = "torso") -> str:
    """Coarse colour word for a clothing region (used as text facts for decision questions, not identity)."""
    H, W = image.shape[:2]
    x0, y0, x1, y1 = box
    h = y1 - y0
    if region == "torso":
        rx0, ry0, rx1, ry1 = torso_region(box)
    else:  # lower body
        rx0, ry0, rx1, ry1 = int(x0), int(y0 + 0.6 * h), int(x1), int(y1)
    rx0, ry0, rx1, ry1 = max(0, rx0), max(0, ry0), min(W, rx1), min(H, ry1)
    if rx1 - rx0 < 1 or ry1 - ry0 < 1:
        return "unknown"
    hsv = cv2.cvtColor(image[ry0:ry1, rx0:rx1], cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    sat = hsv[(hsv[:, 1] > 70) & (hsv[:, 2] > 50)]
    if len(sat) < max(3, 0.25 * len(hsv)):
        v = float(np.median(hsv[:, 2]))
        return "black" if v < 70 else ("white" if v > 190 else "gray")
    hue = float(np.median(sat[:, 0]))
    for a, b, name in COLOR_NAMES:
        if a <= hue < b:
            return name
    return "unknown"
