"""Source packet types shared by every adapter (synthetic, bridge WS, phone WS, OpenCV URL, replay)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..schemas import DroneTelemetry, PhoneObservation, Provenance


@dataclass
class FramePacket:
    source_id: str
    frame_id: int
    image: np.ndarray  # BGR uint8
    capture_ts_ms: float  # pipeline clock (device clock already converted when known)
    receive_ts_ms: float
    provenance: Provenance
    timestamp_quality: str = "exact"  # exact | estimated | arrival | media (file playback time)
    meta: dict[str, Any] = field(default_factory=dict)
    gt: dict[str, Any] | None = None  # simulator ground truth (evaluation only; never read by the pipeline)


@dataclass
class PhonePacket:
    observation: PhoneObservation
    receive_ts_ms: float
    provenance: Provenance
    rgb: np.ndarray | None = None


@dataclass
class TelemetryPacket:
    telemetry: DroneTelemetry
    receive_ts_ms: float
    provenance: Provenance
