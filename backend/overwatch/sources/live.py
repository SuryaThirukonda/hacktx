"""Live adapters: OpenCV URL/webcam/file ingest, the DJI bridge link (iPhone A over /ws/bridge), and the
responder phone uplink (/ws/phone). The pipeline sees the same packet types as in simulation.

Binary frame format (both /ws/bridge and /ws/phone):  [1 byte kind][8 bytes capture_ts_ms float64 big-endian]
[payload]; kind 0x01 = JPEG, 0x02 = H.264 Annex-B access unit. Device timestamps are converted to the server
clock with a server-initiated NTP-style exchange; until an offset exists, arrival time is used and labelled.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from pathlib import Path

import cv2
import numpy as np

from ..clock import OffsetEstimator
from ..schemas import BridgeCommand, DroneTelemetry, PhoneObservation
from .base import FramePacket, PhonePacket, TelemetryPacket

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)


class DeviceClock:
    """Server-initiated sync: server sends t0; device replies with t1 (recv) and t2 (send) in its clock; server
    receives at t3. offset = device - server; server_ts = device_ts - offset."""

    def __init__(self) -> None:
        self.est = OffsetEstimator()

    def add(self, t0: float, t1: float, t2: float, t3: float) -> None:
        self.est.add(t0, t1, t2, t3)

    def to_server(self, device_ts: float) -> float | None:
        off = self.est.offset_ms
        return None if off is None else device_ts - off

    def info(self) -> dict[str, Any]:
        return {"offset_ms": self.est.offset_ms, "min_rtt_ms": self.est.min_rtt_ms, "jitter_ms": self.est.jitter_ms,
                "samples": len(self.est.samples)}


def parse_binary_frame(data: bytes) -> tuple[int, float, bytes]:
    if len(data) < 9:
        raise ValueError("short frame")
    kind = data[0]
    (ts,) = struct.unpack(">d", data[1:9])
    if kind not in (1, 2) or not math.isfinite(ts) or ts < 0:
        raise ValueError("invalid video kind or capture timestamp")
    return kind, ts, data[9:]


def pack_binary_frame(kind: int, ts_ms: float, payload: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">d", ts_ms) + payload


class H264Decoder:
    def __init__(self) -> None:
        import av  # optional dependency (pip install av)

        self.ctx = av.CodecContext.create("h264", "r")

    def decode(self, data: bytes) -> list[np.ndarray]:
        import av

        out = []
        for pkt in self.ctx.parse(data):
            for fr in self.ctx.decode(pkt):
                out.append(fr.to_ndarray(format="bgr24"))
        return out


class LiveBridge:
    """Server-side proxy of the iPhone bridge app connection; implements the autonomy BridgeLink interface."""

    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.out: asyncio.Queue | None = None
        self.connected_since: float | None = None
        self.last_rx_ms = -1e12
        self.audit: deque[dict] = deque(maxlen=200)
        self.sim_active = False
        self.clock = DeviceClock()
        self.decoder: H264Decoder | None = None
        self.frame_id = 0
        self.command_seq = 0
        self.synthetic = False  # a test harness (fake_devices.py) declares synthetic imagery in its hello
        self.legacy = False
        self.control_capable = False
        self.control_inhibited = True
        self.control_token: str | None = None
        self.motion_profile: str | None = None
        self.translation_supported = False
        self.translation_armed = False
        self.web_control_supported = False
        self.pilot_reason: str | None = None
        self.control_error: str | None = None
        self.control_phase: str | None = None
        self.setup_generation = 0

    def reset_connection(self, *, legacy: bool = False) -> None:
        with self.rt.pipeline.lock:
            self.rt.cfg.autonomy.allow_real_flight = False
            self.rt.pipeline.flight_confirmed_track_id = None
            self.rt.pipeline.describe_proposal = None
            self.rt.pipeline.autonomy.set_mode("MANUAL", self.rt.clock.now_ms(), "bridge connection reset: controls off")
        self.legacy = legacy
        self.synthetic = False
        self.sim_active = False
        self.control_capable = False
        self.control_inhibited = True
        self.control_token = None
        self.motion_profile = None
        self.translation_supported = False
        self.translation_armed = False
        self.web_control_supported = False
        self.pilot_reason = None
        self.control_error = None
        self.control_phase = None
        self.setup_generation += 1
        self.clock = DeviceClock()
        self.decoder = None
        self.last_rx_ms = -1e12

    def _pilot_override(self, reason: str) -> None:
        # Invalidate in-flight model answers as well as already selected routes.
        p = self.rt.pipeline
        with p.lock:
            p.autonomy.set_mode("MANUAL", self.rt.clock.now_ms(), reason, emit_command=False)
            p.autonomy.clear_plan()

    def permits(self, cmd: BridgeCommand, *, queued: bool = False) -> bool:
        if self.legacy or not self.control_capable:
            return False
        if cmd.type in ("operator_rearm", "sim_start", "sim_stop"):
            return (self.web_control_supported and self.control_token is not None
                    and (not queued or cmd.payload.get("control_token") == self.control_token))
        stop = cmd.type in ("ping", "estop", "hover") or (cmd.type == "mode" and cmd.payload.get("vstick") is False)
        return stop or (not self.control_inhibited and self.control_token is not None
                        and (not queued or cmd.payload.get("control_token") == self.control_token))

    @property
    def is_sim(self) -> bool:
        return self.sim_active  # DJI simulator running on the aircraft (props off): safe command target

    def connected(self, now_ms: float) -> bool:
        return self.out is not None and now_ms - self.last_rx_ms < 2000.0

    def send(self, cmd: BridgeCommand) -> None:
        q, loop = self.out, self.rt.loop
        if q is None or loop is None:
            return
        if not self.permits(cmd):
            return
        # One wire sequence across setup, mode, gimbal, RTL and setpoints.
        with self.rt.pipeline.lock:
            self.command_seq += 1
            payload = {**cmd.payload, "control_token": self.control_token, "ttl_ms": min(300, cmd.payload.get("ttl_ms", 300))}
            if cmd.type == "vstick":
                payload.update(seq=self.command_seq, ts_ms=cmd.ts_ms)
            cmd = cmd.model_copy(update={"seq": self.command_seq, "payload": payload})
        msg = cmd.model_dump_json()
        loop.call_soon_threadsafe(_put_latest, q, msg)

    # inbound
    def on_text(self, msg: dict[str, Any]) -> None:
        now = self.rt.clock.now_ms()
        self.last_rx_ms = now
        t = msg.get("type")
        if t == "telemetry":
            tel = DroneTelemetry.model_validate(msg)
            srv = self.clock.to_server(tel.capture_ts_ms)
            tel.capture_ts_ms = srv if srv is not None else now
            self.sim_active = bool(tel.sim)
            self.rt.pipeline.process_telemetry(TelemetryPacket(tel, now, "SYNTHETIC" if self.synthetic else "LIVE"))
        elif t == "sync_reply":
            self.clock.add(float(msg["t0"]), float(msg["t1"]), float(msg["t2"]), now)
        elif t in ("audit", "status", "hello"):
            if t == "audit":
                text = str(msg.get("text") or "")
                if any(word in text.lower() for word in ("failed", "refused", "ignored")):
                    self.control_error = text[:500]
                elif "virtual stick ENABLED" in text:
                    self.control_error = None
            if t == "hello":
                self.synthetic = bool(msg.get("synthetic"))
                self.control_capable = not self.legacy and msg.get("pilot_override_protocol") == 1
            if t == "status" and self.control_capable:
                self.motion_profile = msg.get("motion_profile")
                self.control_phase = msg.get("control_phase")
                self.translation_supported = msg.get("translation_supported") is True
                self.translation_armed = msg.get("translation_armed") is True
                self.web_control_supported = msg.get("web_control_supported") is True
                self.pilot_reason = str(msg.get("pilot_reason") or "") or None
                was_inhibited = self.control_inhibited
                previous_token = self.control_token
                token = msg.get("control_token")
                self.control_token = token if isinstance(token, str) and 0 < len(token) <= 128 else None
                self.control_inhibited = msg.get("control_inhibited") is not False or self.control_token is None
                if self.control_inhibited and not was_inhibited:
                    self._pilot_override(str(msg.get("pilot_reason") or "bridge pilot override"))
                elif previous_token is not None and previous_token != self.control_token:
                    self._pilot_override("bridge re-armed: explicitly select FOLLOW to resume")
            if t == "audit" and msg.get("event") == "pilot_override":
                self.control_inhibited = True
                self._pilot_override(str(msg.get("text") or "physical controller override"))
            self.audit.append({"ts_ms": now, "kind": t, "text": str(msg.get("text") or msg)})

    def on_binary(self, data: bytes, received_ms: float | None = None) -> FramePacket | None:
        now = self.rt.clock.now_ms() if received_ms is None else received_ms
        self.last_rx_ms = now
        # Legacy mode is selected by the endpoint, never guessed from image bytes.
        kind, ts, payload = (1, now, data) if self.legacy else parse_binary_frame(data)
        if not payload:
            return None
        frames: list[np.ndarray] = []
        if kind == 0x01:
            img = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                frames = [img]
        elif kind == 0x02:
            if self.decoder is None:
                try:
                    self.decoder = H264Decoder()
                except Exception as e:  # noqa: BLE001
                    log.error("H.264 needs PyAV (pip install av): %s", e)
                    return None
            frames = self.decoder.decode(payload)
        if not frames:
            return None
        srv = self.clock.to_server(ts)
        self.frame_id += 1
        return FramePacket("dji-bridge", self.frame_id, frames[-1], srv if srv is not None else now, now,
                           "SYNTHETIC" if self.synthetic else "LIVE", "exact" if srv is not None else "arrival")


def _put_latest(q: asyncio.Queue, msg: Any) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(msg)


class PhoneLink:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.clock = DeviceClock()
        self.connected = False
        self._last_rgb: np.ndarray | None = None
        self.synthetic = False

    def on_text(self, msg: dict[str, Any]) -> None:
        now = self.rt.clock.now_ms()
        t = msg.get("type")
        if t == "phone_observation":
            obs = PhoneObservation.model_validate(msg)
            srv = self.clock.to_server(obs.capture_ts_ms)
            obs.capture_ts_ms = srv if srv is not None else now
            self.rt.pipeline.process_phone(PhonePacket(obs, now, "SYNTHETIC" if self.synthetic else "LIVE", self._last_rgb))
            self._last_rgb = None
        elif t == "sync_reply":
            self.clock.add(float(msg["t0"]), float(msg["t1"]), float(msg["t2"]), now)
        elif t == "ack" and msg.get("alert_id"):
            self.rt.pipeline.ack_alert(str(msg["alert_id"]))
        elif t == "hello":
            self.synthetic = bool(msg.get("synthetic"))

    def on_binary(self, data: bytes) -> None:
        kind, ts, payload = parse_binary_frame(data)
        if kind == 0x01:
            img = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                self._last_rgb = img


class URLFrameSource:
    """OpenCV VideoCapture reader (RTSP/RTMP/HTTP/webcam index/file). Latest-frame semantics: if processing is
    slower than capture, old frames are dropped (and counted) instead of queueing seconds of latency."""

    def __init__(self, runtime: Runtime, url: str) -> None:
        self.rt = runtime
        self.url = url
        self.is_file = not (url.isdigit() or "://" in url)
        self.provenance = "RECORDED" if self.is_file else "LIVE"
        self._latest: tuple[int, np.ndarray, float] | None = None
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self.frame_id = 0
        self.reconnects = 0
        self.retry_s = 1.0
        self.loop_file = True  # demo files loop; a one-shot import sets this False
        self.finished = False
        self.source_id = "drone"

    def transform(self, frame: np.ndarray) -> tuple[np.ndarray, dict]:
        """Per-frame hook for specialised sources (near-IR); the plain source passes frames through unchanged."""
        return frame, {}

    def _note_reconnect(self) -> None:
        self.reconnects += 1
        try:
            self.rt.pipeline.counters[self.source_id].reconnects = self.reconnects
        except Exception:  # noqa: BLE001
            pass

    def start(self) -> None:
        threading.Thread(target=self._reader, daemon=True, name="url-reader").start()
        threading.Thread(target=self._worker, daemon=True, name="url-worker").start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()

    def _reader(self) -> None:
        while not self._stop.is_set():
            cap = cv2.VideoCapture(int(self.url) if self.url.isdigit() else self.url)
            if not cap.isOpened():
                self._note_reconnect()
                time.sleep(self.retry_s)
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            period = 1.0 / fps if (self.is_file and fps > 0) else 0.0
            nxt = time.monotonic()
            t_start = self.rt.clock.now_ms()
            n_read = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                if period:
                    nxt += period
                    time.sleep(max(0.0, nxt - time.monotonic()))
                self.frame_id += 1
                # files: the capture time is the frame's media time from the start of playback ("media");
                # streams and webcams: the arrival time ("arrival")
                ts = t_start + n_read * 1000.0 / fps if self.is_file else self.rt.clock.now_ms()
                n_read += 1
                with self._cv:
                    if self._latest is not None:
                        self.rt.pipeline.note_dropped("drone")
                    self._latest = (self.frame_id, frame, ts)
                    self._cv.notify()
            cap.release()
            if self.is_file and not self.loop_file:
                self.finished = True
                return
            self._note_reconnect()
            if not self.is_file:
                time.sleep(1.0)

    def _worker(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while self._latest is None and not self._stop.is_set():
                    self._cv.wait(timeout=0.5)
                item, self._latest = self._latest, None
            if item is None:
                continue
            fid, frame, ts = item
            try:
                frame, meta = self.transform(frame)
                self.rt.pipeline.process_drone_frame(FramePacket("url", fid, frame, ts, self.rt.clock.now_ms(),
                                                                 self.provenance, "media" if self.is_file else "arrival",
                                                                 meta=meta or {}))
            except Exception:  # noqa: BLE001
                log.exception("frame processing failed")


class LiveSources:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.bridge = LiveBridge(runtime)
        self.phone = PhoneLink(runtime)
        self.url_source: URLFrameSource | None = None
        self.replay = None

    async def start(self) -> None:
        cfg = self.rt.cfg
        p = self.rt.pipeline
        p.bridge = self.bridge
        p.autonomy.bridge = self.bridge
        if self.rt.mode == "REPLAY":
            from ..replay.player import ReplayPlayer
            from ..schemas import IncidentState

            p.bridge = None
            p.autonomy.bridge = None  # replays never command an aircraft
            name = Path(cfg.session.replay_path).name if cfg.session.replay_path else "replay"
            p.incident = IncidentState(incident_id=f"REPLAY-{name[-6:]}", active=True, consent_confirmed=True)
            self.replay = ReplayPlayer(self.rt, cfg.session.replay_path)
            self.replay.start()
            return
        d = cfg.sources.drone
        adapter = d.adapter
        if adapter == "auto":
            adapter = "url" if d.url else "bridge"
        if adapter == "url" and d.url:
            self.url_source = URLFrameSource(self.rt, str(d.url))
            self.url_source.start()

    async def stop(self) -> None:
        if self.url_source:
            self.url_source.stop()
        if self.replay:
            self.replay.stop()
