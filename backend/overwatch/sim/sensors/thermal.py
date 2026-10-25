"""SIMULATED THERMAL: a false-colour image made from simulator ground truth, for the worlds only.

People are drawn warm, burning cells hot, water cool, on a uniform background. It is not a sensor model and carries
no temperatures; every frame has "SIMULATED THERMAL" burned into it. Near-IR video (phase 16) is a different thing
and is never called thermal.
"""

from __future__ import annotations

import cv2
import numpy as np

LABEL = "SIMULATED THERMAL"


def render(size: tuple[int, int], people_boxes: list[tuple[float, float, float, float]],
           hot_mask: np.ndarray | None = None, cool_mask: np.ndarray | None = None, seed: int = 0) -> np.ndarray:
    w, h = size
    rng = np.random.default_rng(seed)
    g = np.full((h, w), 70, np.float32) + rng.normal(0, 3, (h, w)).astype(np.float32)
    if cool_mask is not None:
        g[cv2.resize(cool_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)] = 40
    if hot_mask is not None:
        g[cv2.resize(hot_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)] = 245
    for x0, y0, x1, y1 in people_boxes:
        cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2)
        cv2.ellipse(g, (cx, cy), (max(2, int((x1 - x0) / 2)), max(3, int((y1 - y0) / 2))), 0, 0, 360, 200, -1)
    g = cv2.GaussianBlur(g, (0, 0), 1.2)
    img = cv2.applyColorMap(np.clip(g, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    cv2.rectangle(img, (0, 0), (w, 26), (0, 0, 0), -1)
    cv2.putText(img, LABEL + " (from simulator ground truth, no temperatures)", (8, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img
