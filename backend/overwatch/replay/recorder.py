"""Session recorder (Rescue Copilot §14.3 layout). Records the *inputs* (frames, phone observations, telemetry)
plus the outputs (events, alerts, decisions, transcript, decimated state) so a session can be replayed through
the same pipeline and audited later.

    sessions/<stamp>_<session>/
      manifest.json  drone.mp4  drone_frames.jsonl  phone.mp4  phone_observations.jsonl
      telemetry.jsonl  events.jsonl  state.jsonl  calibration.json

Recording real people requires an active incident with confirmed consent (privacy guardrail); simulator sessions
are synthetic and always recordable.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import REPO_ROOT, Config
from ..sources.base import FramePacket, PhonePacket, TelemetryPacket

SESSIONS_DIR = REPO_ROOT / "sessions"


class _Video:
    def __init__(self, path: Path, fps: float) -> None:
        self.path = path
        self.fps = fps
        self.writer: cv2.VideoWriter | None = None
        self.count = 0
        self.size: tuple[int, int] | None = None

    def write(self, img: np.ndarray) -> int:
        h, w = img.shape[:2]
        if self.writer is None:
            self.size = (w, h)
            self.writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        if (w, h) != self.size:
            img = cv2.resize(img, self.size)
        self.writer.write(img)
        self.count += 1
        return self.count - 1

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()


class Recorder:
    def __init__(self, cfg: Config, session_id: str, mode: str, scenario: str | None, root: Path | None = None) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = (root or SESSIONS_DIR) / f"{stamp}_{session_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cfg = cfg
        self.drone_video = _Video(self.dir / "drone.mp4", cfg.sources.drone.target_fps)
        self.phone_video = _Video(self.dir / "phone.mp4", cfg.sources.phone.target_hz)
        self.files = {name: (self.dir / f"{name}.jsonl").open("a") for name in
                      ("drone_frames", "phone_observations", "telemetry", "events", "state", "operator")}
        self.started = time.time()
        self.counts = {"drone_frames": 0, "phone_observations": 0, "telemetry": 0, "events": 0, "state": 0, "operator": 0}
        self._last_state_ms = -1e12
        cfg_hash = hashlib.sha1(json.dumps(cfg.public_dict(), sort_keys=True, default=str).encode()).hexdigest()[:12]
        self.manifest: dict[str, Any] = {
            "format": "overwatch-session-v1", "session_id": session_id, "mode": mode, "scenario": scenario,
            "started_unix": self.started, "config_hash": cfg_hash,
            "consent": "synthetic participants" if mode == "SIMULATED" else "operator-confirmed incident consent",
            "files": ["drone.mp4", "drone_frames.jsonl", "phone.mp4", "phone_observations.jsonl", "telemetry.jsonl",
                      "events.jsonl", "state.jsonl", "operator.jsonl"],
        }
        self._write_manifest()
        self.closed = False

    def _write_manifest(self) -> None:
        (self.dir / "manifest.json").write_text(json.dumps(self.manifest, indent=2, default=str))

    def _line(self, name: str, obj: dict[str, Any]) -> None:
        self.files[name].write(json.dumps(obj, default=str, separators=(",", ":")) + "\n")
        self.counts[name] += 1

    # -- inputs -------------------------------------------------------------------------------------------
    def drone_frame(self, pkt: FramePacket) -> None:
        if self.closed:
            return
        with self.lock:
            idx = self.drone_video.write(pkt.image)
            self._line("drone_frames", {"index": idx, "frame_id": pkt.frame_id, "source_id": pkt.source_id,
                                        "capture_ts_ms": pkt.capture_ts_ms, "receive_ts_ms": pkt.receive_ts_ms,
                                        "provenance": pkt.provenance, "timestamp_quality": pkt.timestamp_quality,
                                        "meta": pkt.meta})

    def phone(self, pkt: PhonePacket) -> None:
        if self.closed:
            return
        with self.lock:
            idx = self.phone_video.write(pkt.rgb) if pkt.rgb is not None else None
            self._line("phone_observations", {"rgb_index": idx, "receive_ts_ms": pkt.receive_ts_ms,
                                              "provenance": pkt.provenance, "observation": pkt.observation.model_dump(mode="json")})

    def telemetry(self, pkt: TelemetryPacket) -> None:
        if self.closed:
            return
        with self.lock:
            self._line("telemetry", {"receive_ts_ms": pkt.receive_ts_ms, "provenance": pkt.provenance,
                                     "telemetry": pkt.telemetry.model_dump(mode="json")})

    def operator(self, rec: dict[str, Any]) -> None:
        """Human/scripted operator commands, so a replay can re-apply them at the same moments."""
        if self.closed:
            return
        with self.lock:
            self._line("operator", rec)

    # -- outputs ------------------------------------------------------------------------------------------
    def event(self, msg: dict[str, Any]) -> None:
        if self.closed:
            return
        with self.lock:
            self._line("events", msg)

    def state(self, st: dict[str, Any]) -> None:
        if self.closed:
            return
        t = float(st.get("server_ts_ms", 0.0))
        if t - self._last_state_ms < 500.0:
            return
        self._last_state_ms = t
        with self.lock:
            slim = {k: st[k] for k in ("server_ts_ms", "map_mode", "calibration", "registration", "drone", "responder",
                                       "locks", "kinematics", "autonomy", "alerts_active", "sources") if k in st}
            slim["tracks"] = [{k: t_[k] for k in ("track_id", "state", "ground_xy_m", "ground_error_m", "speed_mps", "role")}
                              for t_ in st.get("tracks", [])]
            self._line("state", slim)

    def close(self) -> dict[str, Any]:
        with self.lock:
            if self.closed:
                return self.manifest
            self.closed = True
            self.drone_video.close()
            self.phone_video.close()
            for f in self.files.values():
                f.close()
            self.manifest.update({"stopped_unix": time.time(), "counts": self.counts,
                                  "duration_s": round(time.time() - self.started, 1)})
            self._write_manifest()
            return self.manifest


def list_sessions(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or SESSIONS_DIR
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), reverse=True):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                info = json.loads(m.read_text())
            except json.JSONDecodeError:
                continue
            info["path"] = str(d)
            info["name"] = d.name
            out.append(info)
    return out
