"""Runtime: owns the pipeline, the sources (simulator or live adapters), decision service and the pub/sub fan-out to
WebSocket clients. In SIMULATED mode everything (ticks, autonomy, decisions) runs on simulation time so pausing or
changing speed is exact; LIVE/REPLAY run on the wall clock."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import orjson

from .clock import Clock, SimClock, WallClock
from .config import Config
from .decisions.router import DecisionRouter, DecisionService
from .evaluation.metrics import Metrics
from .faults import FaultInjector
from .ids import session_id
from .pipeline import Pipeline
from .schemas import Zone
from .evaluation.scenario_runner import OperatorEmulator
from .sim.driver import SimulationDriver

log = logging.getLogger(__name__)


@dataclass
class Hub:
    """Bounded per-subscriber queues; slow consumers lose old messages instead of building latency."""

    subs: set[asyncio.Queue] = field(default_factory=set)
    maxsize: int = 64

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.maxsize)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    def publish(self, msg: Any) -> None:
        for q in list(self.subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


def dumps(obj: Any) -> bytes:
    return orjson.dumps(obj, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)


class Runtime:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.mode = cfg.session.mode
        if self.mode == "LIVE":
            cfg.autonomy.allow_real_flight = False  # Startup never inherits physical permission from a config file.
        self.metrics = Metrics()
        self.faults = FaultInjector()
        self.session_id = session_id()
        self.clock: Clock = SimClock() if self.mode in ("SIMULATED", "REPLAY") else WallClock()
        self.sim: SimulationDriver | None = None
        self.live = None
        bridge = None
        if self.mode == "SIMULATED":
            self.sim = SimulationDriver(cfg, self.faults, cfg.session.scenario)
            bridge = self.sim.bridge
        self.router = DecisionRouter(cfg, self.faults, self.mode)
        self.pipeline = Pipeline(cfg, self.clock, self.mode, self.faults, self.metrics, self.router, bridge, self.session_id)
        self._set_zones()
        self.decisions = DecisionService(self.router, self.pipeline.apply_decision, self.clock.now_ms)
        self.pipeline.decisions = self.decisions
        # street map data for pursuit routing: the simulator world's streets (a real incident: an OpenStreetMap extract)
        self.pipeline.map_provider = (lambda: self.sim.map_data()) if self.sim is not None else None
        if self.sim is not None:  # named world entities for grounding operator commands
            from .voice.grounding import world_entities
            self.pipeline.voice.entities_provider = lambda: world_entities(self.sim.world, self.sim.map_data())
            self.pipeline.crew.problem_provider = lambda: self.sim.crew_problem()
        self.state_hub = Hub(maxsize=8)
        self.event_hub = Hub(maxsize=256)
        self.alert_hub = Hub(maxsize=64)
        self.video_hubs = {"drone": Hub(maxsize=2), "phone": Hub(maxsize=2)}
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ow")
        self.paused = False
        self.speed = 1.0
        self.running = False
        self.tasks: list[asyncio.Task] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.operator: OperatorEmulator | None = None  # scripted demo operator (simulator only)
        self._last_tick_sim = -1e12
        self._last_auto_sim = -1e12
        self._last_render_sim_s = -1.0
        self._video_sent = {"drone": -1, "phone": -1}
        self.recorder = None
        self.started_wall = time.time()
        from .voice.gemini import GeminiNarrator
        from .voice.tts import ElevenLabsTTS

        s = cfg.secrets
        self.tts = ElevenLabsTTS(s.elevenlabs_accounts, s.elevenlabs_voice_id, cfg.voice.elevenlabs_model)
        self.narrator = GeminiNarrator(s.gemini_api_key, cfg.voice.gemini_model)
        self.narration_source = "template"
        # where the (true, simulated) drone was when each D1 decision was applied: pins for the 3D view
        self.decision_marks: dict[str, list[float]] = {}
        self.pipeline.decision_hook = self._mark_decision
        self._recent_frames: list[bytes] = []
        self._restart_request: str | None = None
        from .incident.service import IncidentService
        from .sources.video import VideoSourceManager
        self.video = VideoSourceManager(self)
        self.incidents = IncidentService(self)  # last: it may restore an incident and queue its scenario
        from .incident.tiger import TigerMirror
        self.tiger = TigerMirror(cfg.secrets.tiger_database_url)  # best-effort mirror to Tiger Data (TimescaleDB)
        if self.tiger.enabled:
            self.incidents.store.subscribe(self.tiger.event)
        self._tiger_tel_seq = -1

    def _set_zones(self) -> None:
        if self.sim is not None:
            self.pipeline.zones = [Zone(zone_id=z.zone_id, name=z.name, polygon_xy_m=z.polygon, kind="task")  # type: ignore[arg-type]
                                   for z in self.sim.world.zones]

    # ---------------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.decisions.loop = self.loop
        self.running = True
        self.tiger.start()
        if self.sim is not None:
            self.tasks.append(asyncio.create_task(self._sim_loop(), name="sim"))
        else:
            from .sources.live import LiveSources
            self.live = LiveSources(self)
            await self.live.start()
            if self.mode == "LIVE":
                self.tasks.append(asyncio.create_task(self._wall_ticks(), name="ticks"))
        self.tasks.append(asyncio.create_task(self._publish_loop(), name="publish"))
        self.tasks.append(asyncio.create_task(self._video_loop(), name="video"))
        self.tasks.append(asyncio.create_task(self._probe_loop(), name="probe"))
        self.tasks.append(asyncio.create_task(self._narration_loop(), name="narration"))
        if self.tts.available:
            self.tasks.append(asyncio.create_task(self.tts.warm(), name="tts-warm"))

    async def stop(self) -> None:
        self.running = False
        self.tiger.stop()
        for t in self.tasks:
            t.cancel()
        if self.live is not None:
            await self.live.stop()
        if self.recorder is not None:
            self.stop_recording()
        self.executor.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------------------------------------------------
    def _mark_decision(self, job, rec) -> None:
        if job.task == "D3_describe" and job.context.get("person_mark_request"):
            self.incidents.person_mark.on_decision(job, rec)
        if self.sim is None or job.task != "D1_reacquire":
            return
        d = self.sim.drone
        self.decision_marks[rec.decision_id] = [round(float(v), 2) for v in d.pos]
        if len(self.decision_marks) > 400:
            self.decision_marks.pop(next(iter(self.decision_marks)))

    async def _sim_loop(self) -> None:
        dt = 1.0 / SimulationDriver.WORLD_HZ
        next_wall = time.monotonic()
        loop = asyncio.get_running_loop()
        while self.running:
            if self.paused:
                await asyncio.sleep(0.05)
                next_wall = time.monotonic()
                continue
            now = time.monotonic()
            if now < next_wall:
                await asyncio.sleep(next_wall - now)
            next_wall += dt / max(self.speed, 0.05)
            behind = time.monotonic() - next_wall
            if behind > 2.0:
                next_wall = time.monotonic()  # give up catching up after a long stall
            # when rendering cannot keep up (large worlds on a slow CPU) the simulation catches up without drawing,
            # but never for longer than the video's freshness limit in simulation time: the feed slows down instead
            # of going stale (the pipeline measures frame age on the simulation clock)
            sim_t = self.sim.t_s if self.sim is not None else 0.0
            render = behind < 0.25 or sim_t - self._last_render_sim_s >= 0.3 or sim_t < self._last_render_sim_s
            if render:
                self._last_render_sim_s = sim_t
            try:
                await loop.run_in_executor(self.executor, self._sim_step, dt, render)
            except Exception:  # noqa: BLE001 - keep the simulation alive, surface in logs
                log.exception("simulation step failed")
                await asyncio.sleep(0.2)

    def _new_sim_session(self) -> None:
        sim = self.sim
        assert sim is not None
        p = self.pipeline
        self.session_id = session_id()
        self.clock.reset_to(0.0)  # type: ignore[attr-defined]
        p.reset_session(self.session_id)
        p.detector_override = sim.scenario.detector  # a scenario may ask for YOLO instead of the colour-blob stand-in
        p.autonomy.obstacle_sensor = sim.obstacle_scene if sim.scenario.stack == "jev-x2" else None
        p.autonomy.bridge = sim.bridge
        p.bridge = sim.bridge
        self.operator = OperatorEmulator(p, sim)
        self._last_tick_sim = self._last_auto_sim = -1e12
        self._video_sent = {"drone": -1, "phone": -1}

    def _sim_step(self, dt: float, render: bool) -> None:
        sim = self.sim
        assert sim is not None
        t0 = time.perf_counter()
        if self._restart_request is not None:
            sid = self._restart_request
            self._restart_request = None
            sim.load(sid)
            self.metrics.reset()
            self.faults.clear_manual()
            self._new_sim_session()
        out = sim.step(dt, render)
        p = self.pipeline
        if out.restarted:
            self._new_sim_session()
        self.clock.set_ms(out.t_ms)  # type: ignore[attr-defined]
        t = out.t_ms
        if self.operator is None or self.operator.sim is not sim:
            self.operator = OperatorEmulator(p, sim)
        self.operator.add(t, out.actions)
        if out.telemetry:
            p.process_telemetry(out.telemetry)
        if out.phone:
            p.process_phone(out.phone)
        if out.dropped_frame:
            p.note_dropped("drone")
        if out.drone_frame:
            p.process_drone_frame(out.drone_frame)
        # the same scripted operator as the headless runner: clicks on the named actor, and when the scenario says
        # so, watches the video and confirms (or re-designates) an unverified re-binding after a 1.2 s look
        if self.operator.pending:
            self.operator.run(t)
        self.operator.check_rebind(t)
        if t - self._last_auto_sim >= 1000.0 / self.cfg.autonomy.command_hz - 1:
            self._last_auto_sim = t
            p.autonomy_tick(t)
        if t - self._last_tick_sim >= 100.0 - 1:
            self._last_tick_sim = t
            p.sim_status = {**sim.status(), "speed": self.speed, "paused": self.paused, "gt_available": True}
            p.tick(t)
        self.metrics.observe("sim_step_ms", (time.perf_counter() - t0) * 1000.0)

    def _mirror_to_tiger(self, now: float, n: int) -> None:
        """Telemetry at the state rate (new packets only) and the confirmed tracks a few times a second."""
        p = self.pipeline
        iid = self.incidents.current_id
        tel = p.telemetry
        if tel is not None and tel.seq != self._tiger_tel_seq:
            self._tiger_tel_seq = tel.seq
            a = p.autonomy
            self.tiger.telemetry(iid, self.session_id, tel, a.mode, a.fsm, now)
        if n % 5 == 0:
            self.tiger.detections(iid, self.session_id, p.tracks_snapshot(now))

    async def _wall_ticks(self) -> None:
        loop = asyncio.get_running_loop()
        last_tick = 0.0
        while self.running:
            now = self.clock.now_ms()
            await loop.run_in_executor(self.executor, self.pipeline.autonomy_tick, now)
            if now - last_tick >= 100.0:
                last_tick = now
                await loop.run_in_executor(self.executor, self.pipeline.tick, now)
            await asyncio.sleep(1.0 / self.cfg.autonomy.command_hz)

    async def _narration_loop(self) -> None:
        """Gemini situation summaries when configured; the template narrator stays in place otherwise."""
        while self.running:
            await asyncio.sleep(self.cfg.voice.summary_interval_s)
            if self.cfg.voice.narrator != "gemini" or not self.narrator.available or self.paused:
                continue
            p = self.pipeline
            with p.lock:
                k = p.kin_state
                facts = {"subject_lock": p.locks["subject"].state, "kinematics": k.model_dump(exclude={"predicted_path_xy_m"}),
                         "responder_position_source": p.responder.position_source,
                         "calibration": {"valid": p.calib_status.valid, "source": p.calib_status.source,
                                         "error_m": p.calib_status.error_bound_m},
                         "motion_label_advisory": p.motion_label}
                vf = p.latest_drone
            frames = []
            if vf is not None and self.cfg.privacy.send_images_to_cloud:
                ok, buf = cv2.imencode(".jpg", cv2.resize(vf.image, (480, 270)), [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    frames.append(buf.tobytes())
            text = await self.narrator.summarize(facts, frames)
            if text:
                with p.lock:
                    p.narration = f"{text} [gemini]"

    async def _probe_loop(self) -> None:
        while self.running:
            try:
                await self.router.refresh()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(15.0)

    # ---------------------------------------------------------------------------------------------------------
    async def _publish_loop(self) -> None:
        loop = asyncio.get_running_loop()
        period = 1.0 / self.cfg.server.state_hz
        n = 0
        while self.running:
            t0 = time.monotonic()
            try:
                now = self.clock.now_ms()
                if self.state_hub.subs or self.recorder is not None:
                    st = await loop.run_in_executor(self.executor, self._state_bytes, now)
                    self.state_hub.publish(st)
                    if n % 5 == 0:
                        ex = await loop.run_in_executor(self.executor, lambda: dumps(self.pipeline.extras(now)))
                        self.state_hub.publish(ex)
                for msg in self.pipeline.drain_outbox():
                    b = dumps(msg)
                    self.event_hub.publish(b)
                    if msg.get("type") in ("alert", "ack", "transcript"):
                        self.alert_hub.publish(b)
                    if self.recorder is not None:
                        self.recorder.event(msg)
                    self.incidents.on_outbox(msg)
                if n % 2 == 0:
                    self.alert_hub.publish(dumps(self.responder_view(now)))
                if self.tiger.enabled:
                    self._mirror_to_tiger(now, n)
                if n % 10 == 0:
                    try:
                        self.incidents.sync_tracks()
                        self.incidents.run_missions()
                    except Exception:  # noqa: BLE001
                        log.exception("track history / mission sync failed")
                n += 1
            except Exception:  # noqa: BLE001
                log.exception("publish failed")
            await asyncio.sleep(max(0.0, period - (time.monotonic() - t0)))

    def _state_bytes(self, now: float) -> bytes:
        st = self.pipeline.snapshot(now)
        d = st.model_dump(mode="json")
        if self.recorder is not None:
            self.recorder.state(d)
        return dumps(d)

    def responder_view(self, now: float) -> dict[str, Any]:
        """Compact state for the responder phone (web or native)."""
        p = self.pipeline
        k = p.kin_state
        subj = p.locks["subject"]
        ph = p.phone_obs
        return {"type": "responder_view", "ts_ms": now, "session_id": self.session_id,
                "subject_state": subj.state, "verified": subj.verified,
                "kin": k.model_dump(), "responder": p.responder.model_dump(),
                "sectors": [s.model_dump() for s in ph.sectors] if ph else [],
                "local_alert": bool(ph.local_alert_active) if ph else False,
                "alerts": [a.model_dump() for a in p.arbiter.active_alerts()[:3]],
                "mode": self.mode, "ai_paused": self.router.ai_paused, "profile": p.profile}

    async def _video_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.running:
            for src in ("drone", "phone"):
                hub = self.video_hubs[src]
                vf = self.pipeline.latest_drone if src == "drone" else self.pipeline.latest_phone
                if vf is None or vf.seq == self._video_sent[src] or not hub.subs:
                    continue
                self._video_sent[src] = vf.seq
                try:
                    payload = await loop.run_in_executor(self.executor, self._encode_frame, vf)
                    hub.publish(payload)
                except Exception:  # noqa: BLE001
                    log.exception("video encode failed")
            await asyncio.sleep(1.0 / max(1.0, self.cfg.server.video_max_fps * 2))

    def _encode_frame(self, vf) -> bytes:
        ok, buf = cv2.imencode(".jpg", vf.image, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.server.video_jpeg_quality])
        header = dumps(vf.overlay.model_dump(mode="json"))
        return len(header).to_bytes(4, "big") + header + (buf.tobytes() if ok else b"")

    # ---------------------------------------------------------------------------------------------------------
    # ---------------------------------------------------------------------------------------------------------
    def start_recording(self) -> dict[str, Any]:
        from .replay.recorder import Recorder

        p = self.pipeline
        if self.recorder is not None:
            return {"recording": True, "path": str(self.recorder.dir)}
        if self.mode != "SIMULATED" and not (p.incident.active and p.incident.consent_confirmed):
            raise PermissionError("recording real people requires an active incident with confirmed consent")
        rec = Recorder(self.cfg, self.session_id, self.mode, self.sim.scenario.id if self.sim else None)
        self.recorder = rec
        p.recorder = rec
        p.incident.recording = True
        return {"recording": True, "path": str(rec.dir)}

    def stop_recording(self) -> dict[str, Any]:
        rec = self.recorder
        if rec is None:
            return {"recording": False}
        self.pipeline.recorder = None
        self.recorder = None
        self.pipeline.incident.recording = False
        man = rec.close()
        return {"recording": False, "path": str(rec.dir), "manifest": man}

    def control(self, action: str, value: Any = None) -> dict[str, Any]:
        player = getattr(self.live, "replay", None) if self.live is not None else None
        if player is not None and action in ("pause", "resume", "speed", "seek", "restart", "load"):
            return player.control(action, value)
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "speed":
            self.speed = float(min(4.0, max(0.25, float(value))))
        elif action == "restart":
            if self.sim is not None:
                from .sim.scenarios import get_scenario
                sid = str(value) if value else self.sim.scenario.id
                get_scenario(sid)  # validate before queueing
                self._restart_request = sid
                self.paused = False
        elif action == "pilot_override":
            if self.sim is not None:
                self.sim.bridge.pilot_override()
                self.pipeline.set_autonomy_mode("MANUAL", "pilot stick override")
        else:
            raise ValueError(f"unknown control action {action}")
        return {"paused": self.paused, "speed": self.speed,
                "scenario": self.sim.scenario.id if self.sim else None, "session_id": self.session_id}
