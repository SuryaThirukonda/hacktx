"""Deterministic replay of a recorded session through the same pipeline (Rescue Copilot §14.1, S18).

Packets from drone.mp4/drone_frames.jsonl, phone_observations.jsonl(+phone.mp4) and telemetry.jsonl are merged by
their original capture timestamps and injected on a replay clock (pausable, 0.25-4x, seekable). Replayed inputs are
labelled RECORDED; the original provenance travels in metadata so the honest detector choice is reproduced.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2

from ..schemas import DroneTelemetry, PhoneObservation
from ..sources.base import FramePacket, PhonePacket, TelemetryPacket

if TYPE_CHECKING:
    from ..pipeline import Pipeline
    from ..runtime import Runtime

log = logging.getLogger(__name__)


class SessionReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not (self.path / "manifest.json").exists():
            raise FileNotFoundError(f"not a recorded session: {self.path}")
        self.manifest = json.loads((self.path / "manifest.json").read_text())

    def _jsonl(self, name: str) -> Iterator[dict[str, Any]]:
        p = self.path / f"{name}.jsonl"
        if not p.exists():
            return
        with p.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _drone(self) -> Iterator[tuple[float, int, str, Any]]:
        cap = cv2.VideoCapture(str(self.path / "drone.mp4"))
        try:
            for rec in self._jsonl("drone_frames"):
                ok, img = cap.read()
                if not ok:
                    break
                meta = dict(rec.get("meta") or {})
                meta["origin_provenance"] = rec.get("provenance")
                pkt = FramePacket(rec.get("source_id", "replay-drone"), int(rec["frame_id"]), img, float(rec["capture_ts_ms"]),
                                  float(rec["capture_ts_ms"]), "RECORDED", rec.get("timestamp_quality", "exact"), meta)
                yield (pkt.capture_ts_ms, 2, "drone", pkt)
        finally:
            cap.release()

    def _phone(self) -> Iterator[tuple[float, int, str, Any]]:
        vpath = self.path / "phone.mp4"
        cap = cv2.VideoCapture(str(vpath)) if vpath.exists() else None
        try:
            for rec in self._jsonl("phone_observations"):
                obs = PhoneObservation.model_validate(rec["observation"])
                rgb = None
                if rec.get("rgb_index") is not None and cap is not None:
                    ok, img = cap.read()
                    rgb = img if ok else None
                yield (obs.capture_ts_ms, 1, "phone", PhonePacket(obs, obs.capture_ts_ms, "RECORDED", rgb))
        finally:
            if cap is not None:
                cap.release()

    def _telemetry(self) -> Iterator[tuple[float, int, str, Any]]:
        for rec in self._jsonl("telemetry"):
            tel = DroneTelemetry.model_validate(rec["telemetry"])
            yield (tel.capture_ts_ms, 0, "telemetry", TelemetryPacket(tel, tel.capture_ts_ms, "RECORDED"))

    def _operator(self) -> Iterator[tuple[float, int, str, Any]]:
        for rec in self._jsonl("operator"):
            yield (float(rec["ts_ms"]), 3, "operator", rec)

    def packets(self) -> Iterator[tuple[float, int, str, Any]]:
        return heapq.merge(self._telemetry(), self._phone(), self._drone(), self._operator(), key=lambda x: (x[0], x[1]))

    def duration_ms(self) -> float:
        first = last = None
        for rec in self._jsonl("drone_frames"):
            t = float(rec["capture_ts_ms"])
            first = t if first is None else first
            last = t
        return (last - first) if (first is not None and last is not None) else 0.0


def inject(p: Pipeline, kind: str, pkt: Any) -> None:
    if kind == "operator":
        apply_operator(p, pkt)
        return
    if kind == "telemetry":
        p.process_telemetry(pkt)
    elif kind == "phone":
        pkt.receive_ts_ms = pkt.observation.capture_ts_ms
        p.process_phone(pkt)
    else:
        p.process_drone_frame(pkt)


def apply_operator(p: Pipeline, rec: dict[str, Any]) -> None:
    """Re-apply a recorded operator command. Deterministic inputs reproduce the same track IDs."""
    a = rec.get("action")
    try:
        if a == "lock":
            tid = p.click(rec["u"], rec["v"]) if rec.get("u") is not None else None
            p.lock_track(rec["role"], tid or rec["track_id"], f"replayed {rec.get('locked_by') or 'operator'}", rec.get("description"))
        elif a == "unlock":
            p.unlock(rec["role"])
        elif a == "confirm_candidate":
            p.confirm_candidate(rec["track_id"])
        elif a == "autonomy":
            # replays never command an aircraft; the mode is re-applied for display/logic only
            p.set_autonomy_mode(rec["mode"], f"replayed {rec.get('source', 'operator')}")
        elif a == "inject_conflict":
            p.inject_scripted_events(rec["conflict_id"], rec["specs"], float(rec["ts_ms"]))
    except (KeyError, PermissionError, ValueError) as e:
        log.info("replayed operator action %s skipped: %s", a, e)


def replay_headless(p: Pipeline, path: str | Path, clock) -> int:
    """Process a whole session as fast as possible with the same tick schedule as live; returns packets."""
    n = 0
    last_auto = last_tick = -1e12
    for ts, _, kind, pkt in SessionReader(path).packets():
        clock.set_ms(ts)
        inject(p, kind, pkt)
        n += 1
        if ts - last_auto >= 49.0:
            last_auto = ts
            p.autonomy_tick(ts)
        if ts - last_tick >= 99.0:
            last_tick = ts
            p.tick(ts)
    return n


class ReplayPlayer:
    def __init__(self, runtime: Runtime, path: str | None) -> None:
        self.rt = runtime
        self.path = path
        self.reader: SessionReader | None = None
        self.task: asyncio.Task | None = None
        self.paused = False
        self.speed = 1.0
        self.t0_ms = 0.0
        self.pos_ms = 0.0
        self.duration_ms = 0.0
        self.ended = False
        self._seek_to: float | None = None
        self.error: str | None = None

    def start(self) -> None:
        if not self.path:
            self.error = "no session selected (set session.replay_path or POST /api/replay/control {action: load})"
            return
        self.task = asyncio.get_event_loop().create_task(self._loop())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()

    def load(self, path: str) -> None:
        self.stop()
        self.path = path
        self.ended = False
        self.error = None
        self.rt.pipeline.reset_session()
        self.start()

    def control(self, action: str, value: Any = None) -> dict[str, Any]:
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "speed":
            self.speed = float(min(4.0, max(0.25, float(value))))
        elif action == "seek":
            self._seek_to = max(0.0, float(value) * 1000.0)
        elif action == "restart":
            self._seek_to = 0.0
        elif action == "load":
            self.load(str(value))
        else:
            raise ValueError(f"unknown replay action {action}")
        return self.status()

    def status(self) -> dict[str, Any]:
        return {"scenario": f"replay:{Path(self.path).name}" if self.path else "replay", "scenario_name": "Replay (RECORDED inputs)",
                "t_s": round(self.pos_ms / 1000.0, 2), "duration_s": round(self.duration_ms / 1000.0, 1),
                "speed": self.speed, "paused": self.paused, "gt_available": False}

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        p = self.rt.pipeline
        try:
            self.reader = SessionReader(self.path)  # type: ignore[arg-type]
            self.duration_ms = await loop.run_in_executor(self.rt.executor, self.reader.duration_ms)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.error("replay failed to open %s: %s", self.path, e)
            return
        while True:
            it = self.reader.packets()
            first = next(it, None)
            if first is None:
                self.error = "empty session"
                return
            self.t0_ms = first[0]
            pending: tuple | None = first
            replay_ms = first[0]
            last_wall = time.monotonic()
            last_auto = last_tick = -1e12
            skip_until = self._seek_to + self.t0_ms if self._seek_to else None
            if self._seek_to is not None:
                p.reset_session()
                self._seek_to = None
            self.ended = False
            while pending is not None:
                if self._seek_to is not None:
                    break
                now_wall = time.monotonic()
                if self.paused:
                    last_wall = now_wall
                    await asyncio.sleep(0.05)
                    continue
                replay_ms += (now_wall - last_wall) * 1000.0 * self.speed
                last_wall = now_wall
                if skip_until is not None:
                    replay_ms = max(replay_ms, skip_until)
                batch = []
                while pending is not None and pending[0] <= replay_ms:
                    batch.append(pending)
                    pending = next(it, None)
                if batch:
                    await loop.run_in_executor(self.rt.executor, self._process, batch, skip_until)
                if skip_until is not None and replay_ms >= skip_until:
                    skip_until = None
                self.rt.clock.set_ms(replay_ms)  # type: ignore[attr-defined]
                self.pos_ms = replay_ms - self.t0_ms
                if replay_ms - last_auto >= 50.0:
                    last_auto = replay_ms
                    await loop.run_in_executor(self.rt.executor, p.autonomy_tick, replay_ms)
                if replay_ms - last_tick >= 100.0:
                    last_tick = replay_ms
                    p.sim_status = self.status()
                    await loop.run_in_executor(self.rt.executor, p.tick, replay_ms)
                await asyncio.sleep(0.01)
            if self._seek_to is None:
                self.ended = True
                self.paused = True
                p.sim_status = {**self.status(), "scenario_name": "Replay finished (press resume to loop)"}
                while self.paused and self._seek_to is None:
                    await asyncio.sleep(0.1)
                self._seek_to = 0.0 if self._seek_to is None else self._seek_to

    def _process(self, batch: list[tuple], skip_until: float | None) -> None:
        p = self.rt.pipeline
        for ts, _, kind, pkt in batch:
            if skip_until is not None and ts < skip_until and kind == "drone":
                continue  # fast seek: frames before the target are not analysed
            inject(p, kind, pkt)
