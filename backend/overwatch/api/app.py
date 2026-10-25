"""FastAPI application: REST control plane, WebSocket data plane, MJPEG streams, and the built dashboard."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import anyio
import cv2
import numpy as np
import orjson
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import REPO_ROOT, Config, load_config
from ..faults import FAULT_SPECS
from ..runtime import Runtime, dumps
from ..schemas import BridgeCommand
from ..sim.scenarios import all_scenarios
from ..sim.worlds.manifests import manifest_or_default
from .draw import annotate
from .tunnel import Tunnel

log = logging.getLogger(__name__)


class LockBody(BaseModel):
    track_id: str


class ClickBody(BaseModel):
    u: float
    v: float
    action: str = "select"  # select | lock_subject | lock_responder


class TextBody(BaseModel):
    text: str


class ModeBody(BaseModel):
    mode: str


class DispatchBody(BaseModel):
    description: str  # clothing words, e.g. "red shirt, jeans"; never a face or a name
    last_seen_xy: tuple[float, float] | None = None  # centre of the search area (local metres); default: the scenario's
    radius_m: float = 60.0
    area: list[tuple[float, float]] | None = None  # an explicit search polygon instead of a circle
    altitude_m: float = 28.0

class KeyboardBody(BaseModel):
    session: str
    seq: int = Field(ge=0)
    yaw: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    tilt: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    forward: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    right: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)


class BridgeSetupBody(BaseModel):
    action: Literal["sim_start", "sim_stop", "operator_rearm", "enable_camera_control", "enable_visual_follow", "enable_full_control", "disable_control"]
    props_removed: bool = False


class FaultBody(BaseModel):
    key: str
    value: Any = True


class ControlBody(BaseModel):
    action: str
    value: Any = None


class PersonBody(BaseModel):
    action: str  # goto | drive | stop | resume
    x: float | None = None
    y: float | None = None
    speed: float = 1.4  # walk 1.4, jog 3.0, run 4.5
    heading_deg: float | None = None
    hold_s: float = 0.6  # a "drive" command lapses after this unless renewed (key held down)


class EngineBody(BaseModel):
    primary: str | None = None
    shadow: list[str] | None = None


class AckBody(BaseModel):
    alert_id: str


class GroundPoint(BaseModel):
    u: float
    v: float
    x: float
    y: float
    holdout: bool = False


class GroundPointsBody(BaseModel):
    points: list[GroundPoint]


class TwoPointBody(BaseModel):
    ark: list[tuple[float, float]]
    local: list[tuple[float, float]]


class GeofenceBody(BaseModel):
    polygon: list[tuple[float, float]]


class RecordBody(BaseModel):
    action: str  # start | stop


class IncidentBody(BaseModel):
    incident_id: str | None = None
    consent: bool = False


class VoiceBody(BaseModel):
    text: str
    source: str = "dashboard"  # dashboard | phone | typed


class VoiceAnswer(BaseModel):
    option: int


class VoiceSetting(BaseModel):
    require_approval_sim: bool


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    rt = Runtime(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await rt.start()
        log.info("Overwatch %s running in %s mode (session %s)", __version__, rt.mode, rt.session_id)
        yield
        await rt.stop()

    app = FastAPI(title="Eye in the Sky", version=__version__, lifespan=lifespan)
    app.state.rt = rt
    app.add_middleware(CORSMiddleware, allow_origins=cfg.server.cors_origins, allow_methods=["*"], allow_headers=["*"])
    token = cfg.secrets.overwatch_token

    # Operator commands on the pipeline (locks, autonomy mode, e-stop, engine choice, calibration, simulation) are
    # logged in the current incident; the ones that override automation are marked as overrides.
    commands = ("/api/locks", "/api/autonomy", "/api/decisions/engine", "/api/click", "/api/tracks/",
                "/api/calibration", "/api/registration", "/api/simulation", "/api/session")
    overrides = ("/api/autonomy", "/api/decisions/engine", "/api/locks")

    @app.middleware("http")
    async def log_operator_commands(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        svc = getattr(rt, "incidents", None)
        if request.method == "POST" and path.startswith(commands) and svc is not None and svc.current_id:
            try:
                svc.store.append_event(svc.current_id, "operator_command",
                                       {"path": path, "status_code": response.status_code,
                                        "override": path.startswith(overrides)})
            except Exception:  # noqa: BLE001 - logging must never break a command
                log.exception("operator command logging failed")
        return response

    def auth(request: Request) -> None:
        if not token:
            return
        got = request.query_params.get("token") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if got != token:
            raise HTTPException(401, "access token required")

    def ws_auth(ws: WebSocket) -> bool:
        if not token:
            return True
        got = ws.query_params.get("token") or ws.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return got == token

    P = lambda: rt.pipeline  # noqa: E731

    def ok(**kw: Any) -> dict[str, Any]:
        return {"ok": True, **kw}

    # ---------------------------------------------------------------------------------------------------------
    tunnel = Tunnel(port=int(os.environ.get("OVERWATCH_PORT") or 8000))

    @app.get("/api/tiger")
    def tiger_status() -> dict[str, Any]:
        """The Tiger Data (TimescaleDB) mirror: connection, rows written, queue."""
        return rt.tiger.status()

    @app.get("/api/incidents/{iid}/tiger/metrics")
    def tiger_metrics(iid: str, minutes: int = Query(10, ge=1, le=240)) -> dict[str, Any]:
        """Live rollups for one incident from the continuous aggregates."""
        if not rt.tiger.enabled:
            return {"available": False, "reason": "TIGER_DATABASE_URL is not set"}
        return rt.tiger.metrics(iid, minutes)

    @app.get("/api/tunnel")
    def tunnel_status() -> dict[str, Any]:
        return tunnel.status()

    @app.post("/api/tunnel/start", dependencies=[Depends(auth)])
    def tunnel_start() -> dict[str, Any]:
        return tunnel.start()

    @app.post("/api/tunnel/stop", dependencies=[Depends(auth)])
    def tunnel_stop() -> dict[str, Any]:
        return tunnel.stop()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        now = rt.clock.now_ms()
        st = P().snapshot(now)
        return {"server": "ok", "version": __version__, "mode": rt.mode, "session_id": rt.session_id,
                "uptime_s": round(time.time() - rt.started_wall, 1), "sim_time_ms": now if rt.mode in ("SIMULATED", "REPLAY") else None,
                "sources": [s.model_dump() for s in st.sources], "calibration_valid": st.calibration.valid,
                "map_mode": st.map_mode, "decisions": st.decisions.model_dump() if st.decisions else None,
                "tts": rt.tts.status(), "note": "server alive != sensors alive: check each source status"}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return cfg.public_dict()

    @app.get("/api/session")
    def session() -> dict[str, Any]:
        return {"session_id": rt.session_id, "mode": rt.mode, "scenario": rt.sim.scenario.id if rt.sim else None,
                "incident": P().incident.model_dump(), "paused": rt.paused, "speed": rt.speed}

    @app.post("/api/session", dependencies=[Depends(auth)])
    def new_session(body: ControlBody | None = None) -> dict[str, Any]:
        if rt.sim is None:
            P().reset_session()
            return ok(session_id=rt.session_id, note="new session: tracker epoch advanced, locks cleared")
        return rt.control("restart", body.value if body else None)

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        return [{"id": s.id, "name": s.name, "description": s.description, "duration_s": s.duration_s, "tags": s.tags,
                 "loop": s.loop} for s in all_scenarios().values()]

    @app.get("/api/tracks")
    def tracks(role: str | None = None) -> list[dict[str, Any]]:
        out = [t.model_dump() for t in P().tracks_snapshot(rt.clock.now_ms())]
        return [t for t in out if role is None or t["role"] == role]

    @app.get("/api/tracks/{track_id}")
    def track(track_id: str) -> dict[str, Any]:
        for t in P().tracks_snapshot(rt.clock.now_ms()):
            if t.track_id == track_id:
                evs = [e.model_dump() for e in P().events if e.track_id == track_id][-30:]
                return {"track": t.model_dump(), "events": evs,
                        "colors": P().track_colors.get(track_id), "note": "track IDs are ephemeral, not identities"}
        raise HTTPException(404, "unknown or expired track")

    @app.post("/api/tracks/{track_id}/select")
    def select(track_id: str) -> dict[str, Any]:
        P().select_track(None if track_id == "none" else track_id)
        return ok(selected=track_id)

    @app.post("/api/click", dependencies=[Depends(auth)])
    def click(body: ClickBody) -> dict[str, Any]:
        tid = P().click(body.u, body.v)
        if tid is None:
            return {"ok": False, "track_id": None, "reason": "no track at that point"}
        try:
            if body.action == "lock_subject":
                human_subject(tid)
            elif body.action == "lock_responder":
                P().lock_track("responder", tid)
            else:
                P().select_track(tid)
        except (PermissionError, ValueError) as e:
            raise HTTPException(403, str(e)) from e
        return ok(track_id=tid, action=body.action)

    @app.post("/api/locks/describe", dependencies=[Depends(auth)])
    def describe(body: TextBody) -> dict[str, Any]:
        sent = P().describe(body.text)
        return {"ok": sent, "note": "proposal will appear for operator confirmation" if sent else "no candidates / rate limited"}

    @app.post("/api/locks/{role}", dependencies=[Depends(auth)])
    def lock(role: str, body: LockBody) -> dict[str, Any]:
        try:
            msgs = human_subject(body.track_id) if role == "subject" else P().lock_track(role, body.track_id)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except (KeyError, ValueError) as e:
            raise HTTPException(404, str(e)) from e
        return ok(messages=msgs)

    @app.delete("/api/locks/{role}", dependencies=[Depends(auth)])
    def unlock(role: str) -> dict[str, Any]:
        if role not in ("subject", "responder"):
            raise HTTPException(400, "role must be subject or responder")
        P().unlock(role)
        return ok()

    @app.post("/api/locks/subject/confirm", dependencies=[Depends(auth)])
    def confirm(body: LockBody) -> dict[str, Any]:
        try:
            with P().lock:
                if body.track_id != P().locks["subject"].track_id:
                    raise ValueError("the current subject binding changed")
                svc = rt.incidents
                if svc.current_id and svc.current_id == P().incident.incident_id:
                    svc.require_current_control(svc.current_id)
                    svc.follow_update(svc.current_id)
                    if svc.follow_tracker(svc.current_id).s.status == "unverified":
                        svc.follow_confirm(svc.current_id)
                    else:
                        P().confirm_candidate(body.track_id)
                        svc._confirm_follow_observation(svc.current_id, body.track_id, "operator")
                else:
                    P().confirm_candidate(body.track_id)
        except KeyError as e:
            raise HTTPException(404, "unknown track") from e
        except (ValueError, PermissionError) as e:
            raise HTTPException(409, str(e)) from e
        return ok()

    @app.post("/api/locks/subject/reject", dependencies=[Depends(auth)])
    def reject_rebind() -> dict[str, Any]:
        """'Not them': the operator rejects the subject lock's unverified re-binding."""
        try:
            return ok(events=rt.pipeline.reject_rebind(by="operator"))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e


    @app.post("/api/locks/describe/confirm", dependencies=[Depends(auth)])
    def describe_confirm(body: LockBody) -> dict[str, Any]:
        prop = P().describe_proposal or {}
        try:
            if (prop.get("track_id") != body.track_id or prop.get("session_id") != P().session_id
                    or prop.get("incident_id") != P().incident.incident_id):
                raise PermissionError("description proposal changed; request a current match")
            human_subject(body.track_id, "operator (confirmed text match)")
        except (PermissionError, ValueError, KeyError) as e:
            raise HTTPException(403, str(e)) from e
        P().describe_proposal = None
        return ok()

    @app.get("/api/calibration/status")
    def calib() -> dict[str, Any]:
        return P().calib_status.model_dump()

    @app.post("/api/calibration/ground-points", dependencies=[Depends(auth)])
    def ground_points(body: GroundPointsBody) -> dict[str, Any]:
        try:
            with P().calib_lock:
                st = P().calib.set_manual([(p.u, p.v) for p in body.points], [(p.x, p.y) for p in body.points],
                                          [p.holdout for p in body.points], rt.clock.now_ms())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return st.model_dump()

    @app.post("/api/registration/manual", dependencies=[Depends(auth)])
    def registration(body: TwoPointBody) -> dict[str, Any]:
        try:
            with P().lock:
                st = P().registration.set_manual_two_point(body.ark, body.local)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return st.model_dump()

    @app.post("/api/autonomy/mode", dependencies=[Depends(auth)])
    def autonomy_mode(body: ModeBody) -> dict[str, Any]:
        try:
            with P().lock:
                if rt.mode == "LIVE" and body.mode.upper() in ("FOLLOW", "HOLD"):
                    if not P().autonomy.commands_enabled:
                        raise ValueError("Aircraft controls are off or RC takeover is active. Enable / re-arm controls on the website first.")
                    if body.mode.upper() == "FOLLOW":
                        inp = P()._autonomy_inputs(rt.clock.now_ms())
                        if inp.subject_state != "LOCKED" or not inp.subject_verified or inp.subject_box is None:
                            raise ValueError("Designate a visible person and confirm the current track before Start follow.")
                m = P().set_autonomy_mode(body.mode)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return ok(mode=m, state=P().autonomy.state().model_dump())

    @app.post("/api/autonomy/dispatch", dependencies=[Depends(auth)])
    def autonomy_dispatch(body: DispatchBody) -> dict[str, Any]:
        """Dispatch the simulated drone: take off from where it is (the pad), search an area for a person matching
        the description (clothing words), track them and report their position relative to the pad."""
        try:
            st = P().dispatch(body.description, body.last_seen_xy, body.radius_m, body.area, body.altitude_m, "operator")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except PermissionError as e:
            raise HTTPException(409, str(e)) from e
        return ok(dispatch=st.model_dump())

    @app.post("/api/autonomy/dispatch/cancel", dependencies=[Depends(auth)])
    def autonomy_dispatch_cancel() -> dict[str, Any]:
        P().cancel_dispatch("operator")
        return ok()

    @app.get("/api/autonomy/readiness", dependencies=[Depends(auth)])
    def autonomy_readiness() -> dict[str, Any]:
        with P().lock:
            now, p = rt.clock.now_ms(), P()
            bridge = p.autonomy.bridge
            blockers = []
            if rt.mode == "LIVE":
                if bridge is None or not bridge.connected(now):
                    blockers.append("No current aircraft control connection. Open the SDK FPV screen and tap Start Stream")
                elif not getattr(bridge, "control_capable", False):
                    blockers.append("Video-only SDK relay: update the background relay; images alone cannot carry guarded aircraft control")
                for source, limit in (("drone", cfg.sources.drone.max_age_ms), ("telemetry", cfg.sources.telemetry.max_age_ms)):
                    age = p._age(source, now)
                    if age is None or age > limit:
                        blockers.append(f"Fresh {source} data is missing")
                if getattr(bridge, "control_inhibited", True):
                    blockers.append("Pilot control is latched: neutralize RC controls, then explicitly re-arm")
            if not p.autonomy.commands_enabled:
                blockers.append("Aircraft controls are off: press Enable aircraft controls, or use DJI simulator")
            subject = p.locks["subject"]
            if subject.state != "LOCKED":
                blockers.append("A designated person must be visible before following")
            elif not p._autonomy_inputs(now).subject_verified:
                blockers.append("The current track binding needs explicit confirmation")
            problem = p._real_flight_problem(now)
            if problem and problem not in blockers:
                blockers.append(problem)
            if p.autonomy.mode in ("MANUAL", "ESTOP", "RTL"):
                blockers.append("Designation is not movement: press Start follow after setup; E-STOP/RTL requires manual recovery first")
            d = p.drone_state(now)
            return {"autonomy": p.autonomy.state().model_dump(), "blockers": blockers,
                    "aircraft_reports_vstick_enabled": d.vstick_enabled if d else None,
                    "bridge_capable": bool(getattr(bridge, "control_capable", False)),
                    "web_control_supported": bool(getattr(bridge, "web_control_supported", False)),
                    "bridge_audit": list(getattr(bridge, "audit", []))[-10:]}

    @app.post("/api/autonomy/estop", dependencies=[Depends(auth)])
    def estop() -> dict[str, Any]:
        return ok(mode=P().set_autonomy_mode("ESTOP"))

    @app.post("/api/autonomy/bridge/setup", dependencies=[Depends(auth)])
    async def bridge_setup(body: BridgeSetupBody) -> dict[str, Any]:
        with P().lock:
            bridge = P().autonomy.bridge
            if bridge is not None:
                bridge.setup_generation = getattr(bridge, "setup_generation", 0) + 1
            if body.action == "disable_control":
                P().set_autonomy_mode("MANUAL", "website controls disabled")
                cfg.autonomy.allow_real_flight = False
                return ok(note="Aircraft controls disabled. Pilot in control.")
            if not (rt.mode == "LIVE" and getattr(bridge, "web_control_supported", False)
                    and getattr(bridge, "control_capable", False) and bridge.connected(rt.clock.now_ms())):
                raise HTTPException(409, "No live control relay. Open the updated DJI SDK FPV screen and tap Start Stream.")
            tel = P().telemetry
            age = P()._age("telemetry", rt.clock.now_ms())
            if tel is None or age is None or age > cfg.sources.telemetry.max_age_ms:
                raise HTTPException(409, "Fresh DJI telemetry is required before simulator/re-arm setup")
            if body.action == "sim_start" and (not body.props_removed or (tel.flying and not bridge.is_sim)):
                raise HTTPException(409, "Land the aircraft and confirm propellers removed before starting DJI's simulator")
            if body.action == "operator_rearm" and not bridge.is_sim and not cfg.autonomy.allow_real_flight:
                raise HTTPException(409, "Aircraft controls are off. Press Enable aircraft controls first.")
            setup_generation = getattr(bridge, "setup_generation", 0)
            connection = getattr(bridge, "out", None)
            control_token = getattr(bridge, "control_token", None)
            if body.action in ("enable_camera_control", "enable_visual_follow", "enable_full_control"):
                if body.action != "enable_full_control" and cfg.autonomy.motion_profile == "full":
                    raise HTTPException(409, "Website enable supports yaw and camera tilt only; translation needs full-motion site configuration")
                video_problem = P()._physical_video_problem()
                if video_problem:
                    raise HTTPException(409, video_problem)
                video_age = P()._age("drone", rt.clock.now_ms())
                if video_age is None or video_age > cfg.sources.drone.max_age_ms:
                    raise HTTPException(409, "Start Stream first: fresh drone video is required")
                d = P().drone_state(rt.clock.now_ms())
                if d is None or any(v is None for v in (d.alt_m, d.yaw_deg, d.battery_pct, d.gimbal_pitch_deg)):
                    raise HTTPException(409, "Waiting for DJI altitude, heading, battery and camera-tilt telemetry")
                if d.battery_pct < cfg.autonomy.battery_rtl_pct:
                    raise HTTPException(409, "Battery too low for website aircraft control")
            if body.action == "enable_full_control":
                if cfg.autonomy.motion_profile != "full":
                    raise HTTPException(409, "Full control requires the full profile and actual site configuration at startup")
                problem = P()._full_flight_site_problem(rt.clock.now_ms())
                if problem:
                    raise HTTPException(409, problem)
                cfg.autonomy.follow_max_speed_mps = min(1.0, cfg.autonomy.follow_max_speed_mps)
                cfg.autonomy.reacquire_max_speed_mps = min(1.0, cfg.autonomy.reacquire_max_speed_mps)
                cfg.autonomy.max_vertical_mps = min(0.5, cfg.autonomy.max_vertical_mps)
                cfg.autonomy.max_yaw_rate_dps = min(15.0, cfg.autonomy.max_yaw_rate_dps)
            if body.action in ("enable_visual_follow", "enable_full_control") and not getattr(bridge, "translation_supported", False):
                raise HTTPException(409, "This SDK build does not advertise movement support; update its background relay first")
            P().set_autonomy_mode("MANUAL", "website bridge setup")
            if body.action in ("enable_camera_control", "enable_visual_follow", "enable_full_control"):
                cfg.autonomy.motion_profile = {"enable_full_control": "full", "enable_visual_follow": "visual_follow",
                                               "enable_camera_control": "camera_only"}[body.action]
                cfg.autonomy.allow_real_flight = True  # Explicit website opt-in; never starts follow or takeoff.
        deadline = asyncio.get_running_loop().time() + 2
        while getattr(bridge, "control_phase", None) not in (None, "DISABLED"):
            if asyncio.get_running_loop().time() >= deadline:
                with P().lock:
                    cfg.autonomy.allow_real_flight = False
                raise HTTPException(409, "Waiting for DJI to disable virtual stick. Keep RC neutral and retry Enable.")
            await asyncio.sleep(0.02)
        with P().lock:
            if (getattr(bridge, "setup_generation", 0) != setup_generation or
                    getattr(bridge, "out", None) is not connection or P().autonomy.mode != "MANUAL"):
                raise HTTPException(409, "Setup cancelled by Stop, reconnect or another control request")
            if (getattr(bridge, "control_token", None) != control_token or not bridge.connected(rt.clock.now_ms())
                    or P()._age("telemetry", rt.clock.now_ms()) is None
                    or P()._age("telemetry", rt.clock.now_ms()) > cfg.sources.telemetry.max_age_ms):
                cfg.autonomy.allow_real_flight = False
                raise HTTPException(409, "Setup cancelled by RC takeover, stale telemetry or link loss; retry explicitly")
            bridge.control_error = None
            bridge.send(BridgeCommand(type="operator_rearm" if body.action in ("enable_camera_control", "enable_visual_follow", "enable_full_control") else body.action, ts_ms=rt.clock.now_ms(), payload={
                "allow_real_flight": cfg.autonomy.allow_real_flight,
                "allow_translation": cfg.autonomy.allow_real_flight and cfg.autonomy.motion_profile in ("full", "visual_follow")}))
        return ok(note="Request sent. Phone RC-neutral checks and DJI confirmation must complete before Start follow.")

    @app.post("/api/autonomy/keyboard/start", dependencies=[Depends(auth)])
    def keyboard_start() -> dict[str, Any]:
        with P().lock:
            try:
                problem = P()._real_flight_problem(P().clock.now_ms())
                if problem:
                    raise ValueError(problem)
                session = P().autonomy.start_keyboard(P().clock.now_ms())
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
        return ok(session=session)

    @app.post("/api/autonomy/keyboard/input", dependencies=[Depends(auth)])
    def keyboard_input(body: KeyboardBody) -> dict[str, Any]:
        with P().lock:
            try:
                P().autonomy.keyboard_input(body.session, body.seq, body.yaw, body.tilt, P().clock.now_ms(), body.forward, body.right)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
        return ok()

    @app.post("/api/autonomy/keyboard/stop", dependencies=[Depends(auth)])
    def keyboard_stop(body: KeyboardBody) -> dict[str, Any]:
        with P().lock:
            P().autonomy.stop_keyboard(body.session, P().clock.now_ms())
        return ok()

    @app.post("/api/autonomy/geofence", dependencies=[Depends(auth)])
    def geofence(body: GeofenceBody) -> dict[str, Any]:
        if len(body.polygon) < 3:
            raise HTTPException(422, "geofence needs >= 3 points")
        cfg.autonomy.geofence = [tuple(p) for p in body.polygon]  # type: ignore[misc]
        return ok(geofence=cfg.autonomy.geofence)

    @app.post("/api/decisions/engine", dependencies=[Depends(auth)])
    def engine(body: EngineBody) -> dict[str, Any]:
        try:
            rt.router.set_engines(body.primary, body.shadow)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return rt.router.status().model_dump()

    @app.get("/api/decisions")
    def decisions(limit: int = 50) -> list[dict[str, Any]]:
        return [r.model_dump() for r in list(P().decision_log)[-limit:]]

    @app.post("/api/alerts/ack")
    def ack(body: AckBody) -> dict[str, Any]:
        a = P().ack_alert(body.alert_id)
        if a is None:
            raise HTTPException(404, "unknown alert")
        return ok(alert=a.model_dump())

    @app.get("/api/events")
    def events(limit: int = 100) -> list[dict[str, Any]]:
        return [e.model_dump() for e in list(P().events)[-limit:]]

    @app.get("/api/alerts")
    def alerts(limit: int = 100) -> list[dict[str, Any]]:
        return [a.model_dump() for a in list(P().arbiter.history)[-limit:]]

    @app.get("/api/transcript")
    def transcript(limit: int = 100) -> list[dict[str, Any]]:
        return [t.model_dump() for t in list(P().transcript)[-limit:]]

    @app.get("/api/simulation/faults")
    def faults() -> dict[str, Any]:
        return {"specs": FAULT_SPECS, "active": rt.faults.active(), "manual": rt.faults.manual}

    @app.post("/api/simulation/config", dependencies=[Depends(auth)])
    def set_fault(body: FaultBody) -> dict[str, Any]:
        try:
            rt.faults.set_manual(body.key, body.value)
        except KeyError as e:
            raise HTTPException(422, str(e)) from e
        return {"active": rt.faults.active(), "manual": rt.faults.manual}

    @app.get("/api/simulation/people")
    def sim_people() -> dict[str, Any]:
        """The simulated people (ground truth, simulator only): where they are and whether the operator drives them."""
        if rt.sim is None:
            return {"available": False, "people": []}
        return {"available": True, "people": list(rt.sim.gt_states().values()),
                "note": "simulator ground truth; the pipeline never reads it"}

    @app.post("/api/simulation/people/{actor_id}", dependencies=[Depends(auth)])
    def sim_person(actor_id: str, body: PersonBody) -> dict[str, Any]:
        """Drive a simulated person: walk to a point, keep walking in a direction, stop, or resume the script."""
        if rt.sim is None:
            raise HTTPException(409, "no simulation running")
        try:
            st = rt.sim.control_actor(actor_id, body.action, x=body.x, y=body.y, speed=body.speed,
                                      heading_deg=body.heading_deg, hold_s=body.hold_s)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"ok": True, "person": st}

    @app.post("/api/simulation/control", dependencies=[Depends(auth)])
    def sim_control(body: ControlBody) -> dict[str, Any]:
        try:
            return rt.control(body.action, body.value)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/metrics")
    def metrics() -> dict[str, Any]:
        return {**rt.metrics.snapshot(rt.clock.now_ms()), "note": "latencies in ms; n = samples observed"}

    @app.post("/api/incident", dependencies=[Depends(auth)])
    def incident(body: IncidentBody) -> dict[str, Any]:
        try:
            return P().start_incident(body.incident_id, body.consent).model_dump()
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e

    @app.delete("/api/incident", dependencies=[Depends(auth)])
    def close_incident() -> dict[str, Any]:
        return P().stop_incident().model_dump()

    @app.get("/api/recordings")
    def recordings() -> dict[str, Any]:
        from ..replay.recorder import list_sessions

        return {"recording": rt.recorder is not None, "current": str(rt.recorder.dir) if rt.recorder else None,
                "sessions": list_sessions()[:50]}

    @app.post("/api/recordings", dependencies=[Depends(auth)])
    def record(body: RecordBody) -> dict[str, Any]:
        try:
            return rt.start_recording() if body.action == "start" else rt.stop_recording()
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e

    @app.post("/api/replay/control", dependencies=[Depends(auth)])
    def replay_control(body: ControlBody) -> dict[str, Any]:
        if rt.mode != "REPLAY":
            raise HTTPException(409, "server is not in REPLAY mode (start it with OVERWATCH_MODE=REPLAY "
                                     "OVERWATCH_REPLAY_PATH=sessions/<name>)")
        try:
            return rt.control(body.action, body.value)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/tts", dependencies=[Depends(auth)])
    async def tts(text: str = Query(..., max_length=400)) -> Response:
        if not rt.tts.available:
            raise HTTPException(404, "no ElevenLabs key: use on-device speech")
        audio = await rt.tts.speak(text)
        if audio is None:
            raise HTTPException(502, "TTS failed: use on-device speech")
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})

    # -- operator commands (spoken or typed): transcript -> intent -> primitive; see voice/commands.py ------------

    @app.get("/api/voice")
    def voice_state() -> dict[str, Any]:
        with rt.pipeline.lock:
            return rt.pipeline.voice.view()

    @app.post("/api/voice/command", dependencies=[Depends(auth)])
    def voice_command(body: VoiceBody) -> dict[str, Any]:
        from ..voice.commands import _public_cmd
        r = rt.pipeline.voice.handle(body.text[:300], source=body.source, by="operator")
        return {"command": _public_cmd(r)}

    @app.post("/api/voice/answer", dependencies=[Depends(auth)])
    def voice_answer(body: VoiceAnswer) -> dict[str, Any]:
        from ..voice.commands import _public_cmd
        try:
            return {"command": _public_cmd(rt.pipeline.voice.answer(body.option))}
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/api/voice/primitives/{pid}/{verdict}", dependencies=[Depends(auth)])
    def voice_decide(pid: str, verdict: str) -> dict[str, Any]:
        from ..voice.commands import _public_cmd
        if verdict not in ("approve", "reject"):
            raise HTTPException(400, "approve or reject")
        try:
            return {"command": _public_cmd(rt.pipeline.voice.decide(pid, verdict == "approve", by="operator"))}
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/api/voice/settings", dependencies=[Depends(auth)])
    def voice_settings(body: VoiceSetting) -> dict[str, Any]:
        rt.pipeline.voice.require_approval_sim = body.require_approval_sim
        return rt.pipeline.voice.view()

    @app.get("/api/world/meta")
    def world_meta() -> dict[str, Any]:
        if rt.sim is None:
            return {"available": False}
        w = rt.sim.world
        sc = rt.sim.scenario
        return {"available": True, "name": w.name, "bounds": w.bounds,
                "markers": [{"id": m.marker_id, "xy": m.xy, "size_m": m.size_m} for m in w.markers],
                "holdout_ids": cfg.calibration.markers.holdout_ids, "zones": [z.__dict__ for z in w.zones],
                "geofence": w.geofence, "label": "synthetic orthophoto (simulator)",
                "ortho": f"/api/world/ortho.jpg?w={w.name}", "hazards": sc.hazards is not None,
                "hazard_label": getattr(sc.hazards, "label", None), "launchpad": w.launchpad,
                "dispatch": sc.dispatch, "scenario": sc.id, "manifest": manifest_or_default(sc)}

    _ortho_cache: dict[str, bytes] = {}

    @app.get("/api/world/ortho.jpg")
    def ortho() -> Response:
        if rt.sim is None:
            raise HTTPException(404, "orthophoto only exists in SIMULATED mode")
        name = rt.sim.world.name
        if name not in _ortho_cache:  # one per world: switching scenarios must not show another world's map
            w = rt.sim.world
            res = max(0.05, (w.bounds[1] - w.bounds[0]) / 3000.0)
            img, _ = rt.sim.orthophoto(res)
            ok_, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
            _ortho_cache[name] = buf.tobytes()
        return Response(_ortho_cache[name], media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/world/hazards")
    def world_hazards() -> dict[str, Any]:
        """Water, fire, smoke and keep-out areas now (simulator screening models, labelled)."""
        if rt.sim is None or rt.sim.scenario.hazards is None:
            return {"available": False, "areas": []}
        return {"available": True, "t_s": round(rt.sim.t_s, 1), **rt.sim.scenario.hazards.layers(rt.sim.t_s)}

    @app.get("/api/world/thermal.jpg")
    def world_thermal() -> Response:
        """SIMULATED THERMAL view of the current drone frame, made from simulator ground truth (no temperatures)."""
        from ..sim.sensors.thermal import render as thermal

        if rt.sim is None or not rt.sim.last_gt:
            raise HTTPException(404, "simulated thermal exists only in SIMULATED mode")
        intr = rt.sim.drone_intr
        boxes = [tuple(g["box_xyxy"]) for g in rt.sim.last_gt.get("actors", {}).values()
                 if g.get("box_xyxy") and g.get("visibility", 0) > 0.2]
        hot = cool = None
        hz = rt.sim.scenario.hazards
        if hz is not None:
            from ..sim.hazards.layers import _sample

            cam = rt.sim.drone_camera(0.0)
            step = 8
            us, vs = np.meshgrid(np.arange(0, intr.width, step) + step / 2, np.arange(0, intr.height, step) + step / 2)
            pts, hit = cam.pixel_to_ground(np.column_stack([us.ravel(), vs.ravel()]), max_range_m=400.0)
            xs, ys = np.nan_to_num(pts[:, 0]), np.nan_to_num(pts[:, 1])
            if hasattr(hz, "fire"):
                hot = (_sample(hz.fire.grid, hz.fire.burning(rt.sim.t_s).astype(np.float32), xs, ys, hit) > 0.5).reshape(us.shape)
            if hasattr(hz, "flood"):
                cool = (_sample(hz.flood.grid, hz.flood.depth_grid(rt.sim.t_s), xs, ys, hit) > 0.05).reshape(us.shape)
        img = thermal((intr.width, intr.height), boxes, hot, cool, seed=int(rt.sim.t_s))
        ok_, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ---------------------------------------------------------------------------------------------------------
    async def mjpeg(src: str):
        boundary = b"--frame"
        last = -1
        display = src == "drone" and rt.mode == "SIMULATED"
        if display:
            rt._display_viewers += 1
        try:
            while True:
                vf = (rt._display_drone or P().latest_drone) if display else (P().latest_drone if src == "drone" else P().latest_phone)
                if vf is not None and vf.seq != last:
                    last = vf.seq
                    img = annotate(vf.image, vf.overlay)
                    ok_, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok_:
                        yield boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                await asyncio.sleep(1 / (30 if display else 15))
        finally:
            if display:
                rt._display_viewers -= 1

    @app.get("/streams/{src}.mjpg")
    async def stream(src: str) -> StreamingResponse:
        if src not in ("drone", "phone"):
            raise HTTPException(404)
        return StreamingResponse(mjpeg(src), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/streams/{src}.jpg")
    def snapshot_jpg(src: str, annotated: bool = True) -> Response:
        vf = P().latest_drone if src == "drone" else P().latest_phone
        if vf is None:
            raise HTTPException(404, f"no {src} frame yet")
        img = annotate(vf.image, vf.overlay) if annotated else vf.image
        ok_, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), media_type="image/jpeg")

    # ---------------------------------------------------------------------------------------------------------
    async def pump(ws: WebSocket, hub, binary: bool = False) -> None:
        q = hub.subscribe()
        try:
            while True:
                msg = await q.get()
                if binary:
                    await ws.send_bytes(msg)
                else:
                    await ws.send_text(msg.decode() if isinstance(msg, bytes) else msg)
        finally:
            hub.unsubscribe(q)

    async def drain_incoming(ws: WebSocket, handler=None) -> None:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if handler and msg.get("text"):
                try:
                    handler(orjson.loads(msg["text"]))
                except Exception:  # noqa: BLE001
                    log.exception("bad client message")

    async def serve(ws: WebSocket, hub, binary: bool = False, handler=None, hello: Any = None) -> None:
        await ws.accept()
        if hello is not None:
            await ws.send_text(dumps(hello).decode())
        tasks = [asyncio.create_task(pump(ws, hub, binary)), asyncio.create_task(drain_incoming(ws, handler))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    from .sim3d import register as register_sim3d

    register_sim3d(app, rt)
    from .targets import register as register_targets
    register_targets(app, rt, auth)
    from .incidents import register as register_incidents

    register_incidents(app, rt, auth)

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket) -> None:
        await serve(ws, rt.state_hub, hello={"type": "hello", "session_id": rt.session_id, "mode": rt.mode,
                                              "config": cfg.public_dict(), "faults": FAULT_SPECS})

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        backlog = [e.model_dump() for e in list(P().events)[-60:]] + [d.model_dump() for d in list(P().decision_log)[-40:]] \
            + [a.model_dump() for a in list(P().arbiter.history)[-40:]] + [t.model_dump() for t in list(P().transcript)[-30:]]
        await serve(ws, rt.event_hub, hello={"type": "backlog", "items": backlog})

    def alert_client_msg(msg: dict[str, Any]) -> None:
        if msg.get("type") == "ack" and msg.get("alert_id"):
            P().ack_alert(str(msg["alert_id"]))

    @app.websocket("/ws/alerts")
    async def ws_alerts(ws: WebSocket) -> None:
        await serve(ws, rt.alert_hub, handler=alert_client_msg, hello=rt.responder_view(rt.clock.now_ms()))

    @app.websocket("/ws/video/{src}")
    async def ws_video(ws: WebSocket, src: str) -> None:
        if src not in rt.video_hubs:
            await ws.close(code=4404)
            return
        await serve(ws, rt.video_hubs[src], binary=True)

    @app.websocket("/ws/phone")
    async def ws_phone(ws: WebSocket) -> None:
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        if rt.live is None:
            await ws.accept()
            await ws.send_text(dumps({"type": "error", "detail": "server is in SIMULATED mode; phone uplink ignored"}).decode())
            await ws.close()
            return
        link = rt.live.phone
        await ws.accept()
        link.connected = True
        q = rt.alert_hub.subscribe()

        async def outbound() -> None:
            n = 0
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    await ws.send_text(msg.decode() if isinstance(msg, bytes) else msg)
                except asyncio.TimeoutError:
                    pass
                n += 1
                if n % 5 == 0:
                    await ws.send_text(dumps({"type": "sync", "t0": rt.clock.now_ms()}).decode())

        async def inbound() -> None:
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    return
                if m.get("bytes"):
                    link.on_binary(m["bytes"])
                elif m.get("text"):
                    link.on_text(orjson.loads(m["text"]))

        tasks = [asyncio.create_task(outbound()), asyncio.create_task(inbound())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            rt.alert_hub.unsubscribe(q)
            link.connected = False

    async def bridge_socket(ws: WebSocket, *, legacy: bool = False) -> None:
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        if rt.mode != "LIVE" or rt.live is None:
            await ws.close(code=4403, reason="bridge requires LIVE mode")
            return
        br = rt.live.bridge
        if br.out is not None:
            await ws.close(code=4409, reason="one drone bridge at a time")
            return
        outgoing: asyncio.Queue = asyncio.Queue(maxsize=8)
        br.out = outgoing  # claim ownership before yielding to another connection
        try:
            await ws.accept()
        except BaseException:
            br.out = None
            raise
        br.reset_connection(legacy=legacy)
        br._pilot_override("new bridge connection: previous autonomy cancelled")
        raw: asyncio.Queue = asyncio.Queue(maxsize=32)
        frames: asyncio.Queue = asyncio.Queue(maxsize=1)
        loop = asyncio.get_running_loop()
        reset_decoder = False

        def latest(q, item):
            if q.full():
                q.get_nowait()
                P().note_dropped("drone")
            q.put_nowait(item)

        async def outbound() -> None:
            last_sync = 0.0
            while True:
                try:
                    msg = await asyncio.wait_for(outgoing.get(), timeout=0.5)
                    command = BridgeCommand.model_validate_json(msg)
                    # Recheck on delivery: takeover may have happened after this was queued.
                    if br.permits(command, queued=True):
                        await ws.send_text(msg)
                except asyncio.TimeoutError:
                    pass
                if not legacy and rt.clock.now_ms() - last_sync >= 500:
                    last_sync = rt.clock.now_ms()
                    await ws.send_text(dumps({"type": "sync", "t0": last_sync}).decode())

        async def inbound() -> None:
            nonlocal reset_decoder
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    return
                if m.get("bytes"):
                    data = m["bytes"]
                    if len(data) > 8 * 1024 * 1024:
                        await ws.close(code=1009)
                        return
                    now = rt.clock.now_ms()
                    is_h264 = not legacy and data[0] == 2
                    if raw.full() or not is_h264:
                        while not raw.empty():
                            raw.get_nowait()
                            P().note_dropped("drone")
                        if is_h264:
                            reset_decoder = True
                    raw.put_nowait((data, now))
                elif m.get("text"):
                    try:
                        msg = orjson.loads(m["text"])
                        if not isinstance(msg, dict):
                            raise TypeError("expected a JSON object")
                        br.on_text(msg)
                    except (ValueError, TypeError, KeyError):
                        log.warning("invalid bridge JSON ignored")

        async def decode_video() -> None:
            nonlocal reset_decoder
            while True:
                data, received = await raw.get()
                if reset_decoder:
                    br.decoder = None
                    reset_decoder = False
                    await ws.send_text(dumps({"type": "video_resync"}).decode())
                try:
                    pkt = await asyncio.to_thread(br.on_binary, data, received)
                    if pkt is not None:
                        latest(frames, pkt)
                except Exception:  # corrupt media must not kill telemetry or pilot override
                    br.decoder = None
                    log.warning("invalid bridge video packet ignored", exc_info=True)

        async def perceive() -> None:
            while True:
                pkt = await frames.get()
                if rt.clock.now_ms() - pkt.capture_ts_ms > cfg.sources.drone.max_age_ms:
                    P().note_dropped("drone")
                    continue
                await loop.run_in_executor(rt.executor, P().process_drone_frame, pkt)

        tasks = [asyncio.create_task(f()) for f in (outbound, inbound, decode_video, perceive)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
            br.control_inhibited = True
            br._pilot_override("bridge disconnected: pilot in control")
            br.out = None

    @app.websocket("/ws/bridge")
    async def ws_bridge(ws: WebSocket) -> None:
        await bridge_socket(ws)

    @app.websocket("/ws")
    async def ws_legacy_bridge(ws: WebSocket) -> None:
        """Existing AerialCopilot JPEG relay. Deliberately video-only."""
        await bridge_socket(ws, legacy=True)

    @app.post("/frame", dependencies=[Depends(auth)])
    async def legacy_test_frame(request: Request) -> dict[str, Any]:
        if rt.mode != "LIVE" or rt.live is None:
            raise HTTPException(409, "test frames require LIVE mode")
        stream_active = rt.live.bridge.out is not None
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise HTTPException(413, "frame too large")
        from ..sources.live import LiveBridge
        source = LiveBridge(rt)
        source.legacy = True
        pkt = source.on_binary(bytes(data))
        if pkt is None:
            raise HTTPException(422, "invalid JPEG")
        if stream_active:
            h, w = pkt.image.shape[:2]
            return {"status": "ok", "control": "video_only", "diagnostic_only": True,
                    "stream_active": True, "width": w, "height": h,
                    "note": "Test JPEG received and decoded; active stream and target tracking were left unchanged"}
        await asyncio.get_running_loop().run_in_executor(rt.executor, P().process_drone_frame, pkt)
        return {"status": "ok", "control": "video_only"}

    @app.get("/api/rescue", dependencies=[Depends(auth)])
    def rescue_view() -> dict[str, Any]:
        """The simulated rescue: every beacon placed so far, each with its assessed crew route."""
        with rt.pipeline.lock:
            return rt.pipeline.rescue.view() or {"active": False}

    @app.post("/api/rescue/beacons/{beacon_id}/{decision}", dependencies=[Depends(auth)])
    def rescue_review(beacon_id: str, decision: str) -> dict[str, Any]:
        """A person confirms a beacon (the crew walks to it) or rejects it. Simulation only."""
        if decision not in ("confirm", "reject"):
            raise HTTPException(422, "decision is confirm or reject")
        with rt.pipeline.lock:
            try:
                getattr(rt.pipeline.rescue, decision)("operator", beacon_id)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
            return rt.pipeline.rescue.view() or {"active": False}

    @app.get("/api/voice/{n}.mp3", dependencies=[Depends(auth)])
    @app.get("/api/rescue/voice/{n}.mp3", dependencies=[Depends(auth)])
    async def rescue_voice(n: int):
        """One spoken callout (any mission) as audio, through the server's own ElevenLabs client."""
        text = next((c["speech"] for c in rt.pipeline.callouts if c["n"] == n), None)
        if text is None:
            raise HTTPException(404, "no such callout in this session")
        if not rt.tts.available:
            raise HTTPException(503, "use browser speech; ElevenLabs is not configured")
        audio = await rt.tts.speak(text)
        if not audio:
            raise HTTPException(503, "voice service unavailable")
        return Response(audio, media_type="audio/mpeg")

    dist = REPO_ROOT / "dashboard" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            if path.startswith(("api/", "ws/")):
                raise HTTPException(404, "unknown API path")
            f = (dist / path).resolve()
            if not f.is_relative_to(dist.resolve()):
                raise HTTPException(404, "unknown asset")
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    def human_subject(track_id: str, by: str = "operator") -> list[str]:
        with P().lock:
            svc = rt.incidents
            if svc.current_id and svc.current_id == P().incident.incident_id:
                svc.follow_designate(svc.current_id, track_id, by)
                return ["Human-confirmed subject linked to incident evidence; Start Follow is separate"]
            return P().lock_track("subject", track_id, by)
    return app
