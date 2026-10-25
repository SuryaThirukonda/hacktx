"""Near-infrared video: a camera, recorded file or stream that sees reflected near-IR light (about 750 to 1000 nm).

Near-IR is not thermal. It shows reflected light (from IR illuminators, moonlight, the sun), not heat, so nothing
here reports or implies a temperature. Optional enhancement (denoise, CLAHE, gamma, contrast stretch) is image
processing and is labelled as such wherever its output appears; the raw frame is always available next to it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from .live import URLFrameSource

if TYPE_CHECKING:
    from ..runtime import Runtime

PROCESSING_LABEL = "IMAGE PROCESSING (near-IR, not thermal; no temperatures)"


@dataclass
class Enhancement:
    enabled: bool = False
    denoise: bool = True
    clahe: bool = True
    clahe_clip: float = 2.5
    gamma: float = 0.8  # < 1 brightens shadows
    contrast_stretch: bool = True

    def steps(self) -> list[str]:
        if not self.enabled:
            return []
        out = []
        if self.denoise:
            out.append("denoise (bilateral)")
        if self.clahe:
            out.append(f"CLAHE (clip {self.clahe_clip})")
        if abs(self.gamma - 1.0) > 1e-3:
            out.append(f"gamma {self.gamma}")
        if self.contrast_stretch:
            out.append("contrast stretch (1st to 99th percentile)")
        return out


def to_mono(frame: np.ndarray) -> np.ndarray:
    """Near-IR sensors are effectively monochrome; colour channels from a converted camera carry no extra detail."""
    return frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def enhance(frame: np.ndarray, e: Enhancement) -> tuple[np.ndarray, list[str]]:
    g = to_mono(frame)
    if not e.enabled:
        return g, []
    if e.denoise:
        g = cv2.bilateralFilter(g, 5, 30, 5)
    if e.clahe:
        g = cv2.createCLAHE(clipLimit=e.clahe_clip, tileGridSize=(8, 8)).apply(g)
    if abs(e.gamma - 1.0) > 1e-3:
        lut = np.clip(((np.arange(256) / 255.0) ** e.gamma) * 255.0, 0, 255).astype(np.uint8)
        g = cv2.LUT(g, lut)
    if e.contrast_stretch:
        lo, hi = np.percentile(g, (1, 99))
        if hi > lo + 1:
            g = np.clip((g.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return g, e.steps()


def side_by_side(frame: np.ndarray, e: Enhancement, width: int = 640) -> np.ndarray:
    raw = to_mono(frame)
    proc, steps = enhance(frame, Enhancement(**{**asdict(e), "enabled": True}) if not e.enabled else e)
    h = int(raw.shape[0] * width / raw.shape[1])
    a = cv2.cvtColor(cv2.resize(raw, (width, h)), cv2.COLOR_GRAY2BGR)
    b = cv2.cvtColor(cv2.resize(proc, (width, h)), cv2.COLOR_GRAY2BGR)
    for img, text in ((a, "RAW near-IR"), (b, PROCESSING_LABEL)):
        cv2.rectangle(img, (0, 0), (width, 24), (0, 0, 0), -1)
        cv2.putText(img, text, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.hstack([a, b])
    foot = np.zeros((22, out.shape[1], 3), np.uint8)
    cv2.putText(foot, "steps: " + (", ".join(steps) or "none"), (8, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (220, 220, 220), 1, cv2.LINE_AA)
    return np.vstack([out, foot])


class NearIRVideoSource(URLFrameSource):
    """A file, webcam or stream treated as near-IR: frames go to the pipeline as 3-channel mono, enhanced only when
    the operator turns enhancement on (and the frame says so in its metadata)."""

    kind = "near_ir"

    def __init__(self, runtime: "Runtime", url: str) -> None:
        super().__init__(runtime, url)
        self.enhancement = Enhancement()
        self.last_raw: np.ndarray | None = None

    def transform(self, frame: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        self.last_raw = frame
        g, steps = enhance(frame, self.enhancement)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), {"sensor": "near_ir", "image_processing": steps,
                                                       "label": PROCESSING_LABEL if steps else "near-IR (raw)"}

    def describe(self) -> dict[str, Any]:
        return {"kind": "near_ir", "url": self.url, "enhancement": asdict(self.enhancement),
                "processing_steps": self.enhancement.steps(), "label": PROCESSING_LABEL,
                "note": "reflected near-infrared light; not thermal and never a temperature"}
