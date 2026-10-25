"""Drawing the camera overlay: what the pipeline found, in a form any process can draw (plain tuples from
``Run.hud_items``), so the frames of the 30 fps video can be rendered in worker processes.

Detections arrive 0.2 s after the frame they were found in, so each box is pinned to where it is on the ground and
drawn where that spot is in the current frame; the followed person gets the tracker's bracket, predicted to the
current instant. The simulator views add outlines on the ground (what the drone's camera covers), tags (the drone,
the police car) and a small live copy of the drone's camera in the corner.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img: np.ndarray, text: str, org: tuple[int, int], col, scale: float = 0.45) -> None:
    text = text.replace("°", " deg")  # the Hershey fonts are ASCII only
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, col, 1, cv2.LINE_AA)


def _drone_icon(img: np.ndarray, x: float, y: float, heading: float, col) -> None:
    """A quadcopter symbol, nose up the picture when heading north (the bird's-eye view is north up)."""
    h = math.radians(heading)
    f, r = np.array([math.sin(h), -math.cos(h)]), np.array([math.cos(h), math.sin(h)])  # image forward, right
    c = np.array([x, y])
    for a, b in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        m = c + (f * a + r * b) * 9.0
        cv2.line(img, tuple(np.round(c).astype(int)), tuple(np.round(m).astype(int)), (0, 0, 0), 4, cv2.LINE_AA)
        cv2.line(img, tuple(np.round(c).astype(int)), tuple(np.round(m).astype(int)), col, 2, cv2.LINE_AA)
        cv2.circle(img, tuple(np.round(m).astype(int)), 5, col, 1, cv2.LINE_AA)
    tip = c + f * 19.0
    cv2.line(img, tuple(np.round(c).astype(int)), tuple(np.round(tip).astype(int)), col, 2, cv2.LINE_AA)
    cv2.circle(img, tuple(np.round(c).astype(int)), 22, col, 1, cv2.LINE_AA)


def draw_hud(img: np.ndarray, items: dict) -> np.ndarray:
    out = img.copy()
    H = out.shape[0]
    for pts, col, thick, label in items.get("polys", []):
        ip = np.round(np.array(pts, float) * 4).astype(np.int32)
        if np.abs(ip).max() < 2 ** 26:
            cv2.polylines(out, [ip], True, col, thick, cv2.LINE_AA, shift=2)
            top = min(pts, key=lambda q: q[1])
            if label and 0 <= top[0] < out.shape[1] and 14 <= top[1] < H:
                _text(out, label, (int(top[0]) + 6, int(top[1]) - 6), col, 0.42)
    for x, y, col, label, kind, heading in items.get("tags", []):
        if not (-50 <= x < out.shape[1] + 50 and -50 <= y < H + 50):
            continue
        if kind == "drone":
            _drone_icon(out, x, y, heading, col)
            _text(out, label, (int(x) + 26, int(y) + 5), col)
        elif kind == "truth":
            cv2.circle(out, (int(x), int(y)), 9, col, 1, cv2.LINE_AA)
            _text(out, label, (int(x) + 14, int(y) - 12), col, 0.4)
        elif kind == "right":
            _text(out, label, (int(x) + 26, int(y) + 5), col)
        else:
            _text(out, label, (int(x - 4 * len(label)), int(y)), col)
    for x0, y0, x1, y1, col, thick, label in items.get("boxes", []):
        p0, p1 = (int(round(x0)), int(round(y0))), (int(round(x1)), int(round(y1)))
        cv2.rectangle(out, p0, p1, col, thick, cv2.LINE_AA)
        if label:
            cv2.putText(out, label, (p0[0], max(12, p0[1] - 5)), FONT, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(out, label, (p0[0], max(12, p0[1] - 5)), FONT, 0.42, col, 1, cv2.LINE_AA)
    for x0, y0, x1, y1, col, label in items.get("brackets", []):
        x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
        k = max(6, (x1 - x0) // 3)
        for (ax, ay), (dx, dy) in (((x0, y0), (1, 1)), ((x1, y0), (-1, 1)), ((x0, y1), (1, -1)), ((x1, y1), (-1, -1))):
            cv2.line(out, (ax, ay), (ax + dx * k, ay), col, 2, cv2.LINE_AA)
            cv2.line(out, (ax, ay), (ax, ay + dy * k), col, 2, cv2.LINE_AA)
        cv2.putText(out, label, (x0, y1 + 15), FONT, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, label, (x0, y1 + 15), FONT, 0.45, col, 1, cv2.LINE_AA)
    texts = items.get("texts", [])
    for text, y in zip(texts, (22, H - 12)):
        _text(out, text, (12, y), (240, 240, 240), 0.55)
    return out


def paste_inset(img: np.ndarray, small: np.ndarray, label: str = "DRONE CAMERA (live)") -> np.ndarray:
    """The drone's own camera, small, in the top-right corner of a simulator view."""
    h, w = small.shape[:2]
    x0, y0 = img.shape[1] - w - 14, 38
    img[y0:y0 + h, x0:x0 + w] = small
    cv2.rectangle(img, (x0 - 2, y0 - 2), (x0 + w + 1, y0 + h + 1), (235, 235, 235), 2, cv2.LINE_AA)
    _text(img, label, (x0 + 4, y0 + h + 16), (235, 235, 235), 0.42)
    return img


def encode_jpeg(img: np.ndarray, quality: int = 82) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
