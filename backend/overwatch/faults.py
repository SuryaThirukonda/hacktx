"""Fault injection shared by the simulator, live ingest and decision engines.

Faults come from two places: scripted scenario windows and manual toggles from the dashboard. Manual toggles win.
Every injected fault is visible in /ws/state so a judge can see exactly what was broken on purpose.
"""

from __future__ import annotations

import threading
from typing import Any

FAULT_SPECS: dict[str, dict[str, Any]] = {
    "drone_video_drop": {"type": "bool", "label": "Drone video lost", "group": "sources"},
    "phone_drop": {"type": "bool", "label": "Responder phone offline", "group": "sources"},
    "phone_video_drop": {"type": "bool", "label": "Phone camera feed lost", "group": "sources"},
    "telemetry_drop": {"type": "bool", "label": "Drone telemetry lost", "group": "sources"},
    "lidar_dropout": {"type": "bool", "label": "LiDAR low confidence", "group": "phone"},
    "lidar_unsupported": {"type": "bool", "label": "No LiDAR on device", "group": "phone"},
    "arkit_limited": {"type": "bool", "label": "ARKit tracking limited", "group": "phone"},
    "phone_portrait": {"type": "bool", "label": "Phone held upright (portrait)", "group": "phone"},
    "frame_drop_pct": {"type": "float", "label": "Frame drops", "min": 0.0, "max": 0.5, "group": "video"},
    "video_latency_ms": {"type": "float", "label": "Extra video latency (ms)", "min": 0.0, "max": 1500.0, "group": "video"},
    "low_light": {"type": "bool", "label": "Low light", "group": "video"},
    "blur": {"type": "bool", "label": "Motion blur", "group": "video"},
    "wobble": {"type": "bool", "label": "Camera wobble", "group": "video"},
    "gimbal_pan": {"type": "bool", "label": "Gimbal pan (calibration break)", "group": "video"},
    "markers_hidden": {"type": "bool", "label": "Markers covered", "group": "video"},
    "detector_miss_pct": {"type": "float", "label": "Detector misses", "min": 0.0, "max": 0.6, "group": "video"},
    "wind_gust": {"type": "bool", "label": "Wind gusts", "group": "drone"},
    "gps_degraded": {"type": "bool", "label": "GPS degraded", "group": "drone"},
    "bridge_link_loss": {"type": "bool", "label": "Mac-bridge link loss", "group": "drone"},
    "api_timeout": {"type": "bool", "label": "Decision API timeout", "group": "cloud"},
    "api_429": {"type": "bool", "label": "Decision API rate-limited (429)", "group": "cloud"},
    "api_refusal": {"type": "bool", "label": "Decision API refusal", "group": "cloud"},
    "network_loss": {"type": "bool", "label": "Internet down (all cloud)", "group": "cloud"},
}


class FaultInjector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.manual: dict[str, Any] = {}
        self.scripted: dict[str, Any] = {}

    def set_manual(self, key: str, value: Any) -> None:
        if key not in FAULT_SPECS:
            raise KeyError(f"unknown fault {key!r}")
        with self._lock:
            if value in (False, None, 0, 0.0):
                self.manual.pop(key, None)
            else:
                self.manual[key] = value

    def clear_manual(self) -> None:
        with self._lock:
            self.manual.clear()

    def set_scripted(self, active: dict[str, Any]) -> None:
        with self._lock:
            self.scripted = dict(active)

    def active(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self.scripted)
            out.update(self.manual)
            return out

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self.manual:
                return self.manual[key]
            return self.scripted.get(key, default)

    def cloud_fault(self) -> str | None:
        """The cloud failure mode currently injected, if any (network loss dominates)."""
        a = self.active()
        for k in ("network_loss", "api_timeout", "api_429", "api_refusal"):
            if a.get(k):
                return k
        return None
