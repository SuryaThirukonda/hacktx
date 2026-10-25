"""Incident-scoped REST API and event stream (``/api/incidents/...``, ``/ws/incidents/{id}``).

Errors use one shape: ``{"error": {"code", "message", "details"}}``. Video stays on the existing binary video
sockets; this stream carries incident events only.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Literal

import cv2
import numpy as np
from fastapi import Depends, FastAPI, Query, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from .. import __version__
from ..decisions.recommendations import ProposalRejected
from ..incident.models import (Alert, BoundingBox, FrameMismatch, Position, Review, SceneAnchor, Transform,
                               UTCDateTime, utcnow)
from ..incident.models import Observation as ObservationModel
from ..incident.store import NotFound
from ..runtime import dumps


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def err(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details}}, status_code=status)


# --------------------------------------------------------------------------------------------------------------
# Bodies
# --------------------------------------------------------------------------------------------------------------
class IncidentCreate(BaseModel):
    title: str = "Training incident"
    mission_mode: Literal["search", "follow", "structure", "indoor"] = "search"
    scenario_id: str | None = None
    operator_notes: str = ""


class MissionBody(BaseModel):
    mission: Literal["search", "follow", "structure", "indoor"]
    by: str = "operator"


class ObservationIn(BaseModel):
    sensor_source_id: str = "manual"
    observed_at: UTCDateTime | None = None
    category: Literal["person", "opening", "hazard", "supply", "vehicle", "marker", "other"]
    label: str
    confidence: float | None = None
    detector: str | None = None
    bounding_box: BoundingBox | None = None
    world_position: Position | None = None
    horizontal_uncertainty_m: float | None = None
    position_estimation_method: Literal["none", "ground_plane", "manual", "simulation_truth", "lidar", "anchor"] = "none"
    evidence_frame_reference: str | None = None
    stale_after_s: float | None = None
    provenance: Literal["live", "replay", "simulated", "manual"] = "manual"
    attributes: dict[str, Any] = Field(default_factory=dict)


class ReviewBody(BaseModel):
    status: Literal["confirmed", "rejected", "pending"]
    reviewer: str = "operator"
    note: str = ""
    corrected_label: str | None = None


class AlertCreate(BaseModel):
    severity: Literal["info", "attention", "warning", "critical"] = "attention"
    message: str
    source_observation_ids: list[str] = Field(default_factory=list)


class AckIn(BaseModel):
    by: str = "responder"


class ResponderPosition(BaseModel):
    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None
    heading_deg: float | None = None
    heading_accuracy_deg: float | None = None
    enu: Position | None = None
    source: Literal["browser_geolocation", "arkit", "manual", "simulated"] = "browser_geolocation"


class AnchorIn(BaseModel):
    name: str
    type: str
    position: Position
    uncertainty_m: float | None = None
    manually_verified: bool = False
    observation_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class SourceBody(BaseModel):
    source: Literal["simulated", "file", "webcam", "rtsp", "dji_mini2", "near_ir"]
    value: str | None = None


class FromTrackBody(BaseModel):
    track_id: str
    category: Literal["person", "opening", "hazard", "supply", "vehicle", "other"] = "person"
    label: str = "possible person"
    flagged_by: str = "operator"


class ProposalIn(BaseModel):
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    proposed_by: Literal["model", "human"] = "model"
    engine: str | None = None
    rationale: str | None = Field(default=None, max_length=300)


class DecideBody(BaseModel):
    by: str = "operator"


class TextIn(BaseModel):
    text: str = ""


class PersonMarkIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PersonMarkRef(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)


class FollowBody(BaseModel):
    session_id: str | None = None
    designation: int | None = None
    track_id: str | None = None
    by: str = "operator"


class SideABody(BaseModel):
    building_id: str
    compass: Literal["N", "E", "S", "W"]
    by: str = "operator"


class GeorefBody(BaseModel):
    origin_lat: float = Field(ge=-90, le=90)
    origin_lon: float = Field(ge=-180, le=180)
    north_offset_deg: float = 0.0
    note: str = "set by the operator"


class ReplayBody(BaseModel):
    value: Any = None
    title: str | None = None  # restart with another scenario of the same world: the incident's new title


def register(app: FastAPI, rt, auth: Callable[[Request], None]) -> None:
    svc = rt.incidents
    st = svc.store

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        return err(e.status, e.code, e.message, e.details)

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except NotFound as e:
            raise ApiError(404, "not_found", f"{e.args[0]} not found") from e
        except FrameMismatch as e:
            raise ApiError(422, "frame_mismatch", str(e)) from e
        except ProposalRejected as e:
            raise ApiError(422, e.code, e.reason) from e
        except ValidationError as e:
            raise ApiError(422, "validation_error", "request failed validation",
                           [{"loc": x["loc"], "msg": x["msg"]} for x in e.errors()]) from e
        except (ValueError, PermissionError) as e:
            raise ApiError(400, "bad_request", str(e)) from e

    def incident_or_404(iid: str):
        return guard(lambda: st.get_incident(iid))

    # -- health ---------------------------------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, Any]:
        cur = svc.current()
        return {"ok": True, "version": __version__, "mode": rt.mode, "session_id": rt.session_id,
                "current_incident": cur.id if cur else None, "db": st.path,
                "sources": [svc.drone_source().model_dump(mode="json")]}

    # -- incidents ------------------------------------------------------------------------------------------
    @app.post("/api/incidents", dependencies=[Depends(auth)])
    def create_incident(body: IncidentCreate) -> dict[str, Any]:
        inc = guard(lambda: svc.create(body.title, body.mission_mode, body.scenario_id, body.operator_notes))
        return inc.model_dump(mode="json")

    @app.get("/api/incidents")
    def list_incidents() -> dict[str, Any]:
        cur = svc.current_id
        return {"current": cur, "items": [i.model_dump(mode="json") for i in st.list_incidents()]}

    @app.get("/api/incidents/{iid}")
    def get_incident(iid: str) -> dict[str, Any]:
        inc = incident_or_404(iid)
        return {**inc.model_dump(mode="json"), "current": iid == svc.current_id,
                "sources": [s.model_dump(mode="json") for s in st.list_sources(iid)]}

    @app.post("/api/incidents/{iid}/mission", dependencies=[Depends(auth)])
    def set_mission(iid: str, body: MissionBody) -> dict[str, Any]:
        return guard(lambda: st.set_mission(iid, body.mission, body.by)).model_dump(mode="json")

    # -- observations ---------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/observations")
    def list_observations(iid: str, status: str | None = None) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [v.model_dump(mode="json") for v in st.list_observations(iid, status)]}

    @app.post("/api/incidents/{iid}/observations", dependencies=[Depends(auth)])
    def add_observation(iid: str, body: ObservationIn) -> dict[str, Any]:
        incident_or_404(iid)
        from datetime import timedelta

        def make():
            observed = body.observed_at or utcnow()
            data = body.model_dump(exclude={"observed_at", "stale_after_s"})
            obs = ObservationModel(incident_id=iid, observed_at=observed,
                                   stale_after=observed + timedelta(seconds=body.stale_after_s) if body.stale_after_s else None,
                                   **data)
            return st.add_observation(obs)
        return guard(make).model_dump(mode="json")

    @app.post("/api/incidents/{iid}/observations/from-track", dependencies=[Depends(auth)])
    def observation_from_track(iid: str, body: FromTrackBody) -> dict[str, Any]:
        o = guard(lambda: svc.observation_from_track(iid, body.track_id, body.category, body.label, body.flagged_by))
        return o.model_dump(mode="json")

    @app.get("/api/evidence/{name}")
    def evidence(name: str):
        from fastapi.responses import FileResponse

        f = svc.evidence_dir() / name
        if "/" in name or ".." in name or not f.is_file():
            raise ApiError(404, "not_found", "no such evidence image")
        return FileResponse(f, media_type="image/jpeg")

    @app.get("/api/perception")
    def perception() -> dict[str, Any]:
        """Which detectors exist on this machine and which one is running (honest availability)."""
        import importlib.util

        from ..config import REPO_ROOT

        p = rt.pipeline
        det = p.detector
        active = det.model_id if det is not None else "not started (no frame yet)"
        weights = rt.cfg.perception.yolo_weights
        wpaths = [REPO_ROOT / "backend" / weights, REPO_ROOT / "backend" / "weights" / weights]
        ultra = importlib.util.find_spec("ultralytics") is not None
        rfd = importlib.util.find_spec("rfdetr") is not None
        return {
            "active": active,
            "configured": rt.cfg.perception.detector,
            "unavailable": active.startswith("none ("),
            "detectors": [
                {"name": "sim_blob", "installed": True, "use": "simulator imagery only (labelled SIMULATED)",
                 "calibrated_confidence": False, "license": "this repository"},
                {"name": "ultralytics", "installed": ultra, "weights": weights,
                 "weights_present": any(w.exists() for w in wpaths),
                 "use": "real RGB video (person and vehicle classes)", "calibrated_confidence": False,
                 "license": "AGPL-3.0 unless commercially licensed"},
                {"name": "rfdetr", "installed": rfd, "use": "optional alternative", "calibrated_confidence": False,
                 "license": "Apache-2.0"},
            ],
            "note": "Detector scores are model confidences, not calibrated probabilities; no detection does not mean "
                    "an area is clear.",
        }

    @app.get("/api/usecases")
    def usecases() -> dict[str, Any]:
        from ..usecases import usecases_with_scenarios

        return {"mode": rt.mode, "active_scenario": rt.sim.scenario.id if rt.sim else None,
                "items": usecases_with_scenarios()}

    @app.post("/api/observations/{oid}/review", dependencies=[Depends(auth)])
    def review(oid: str, body: ReviewBody) -> dict[str, Any]:
        v = guard(lambda: st.review(Review(observation_id=oid, **body.model_dump())))
        return v.model_dump(mode="json")

    # -- tracks ---------------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/tracks")
    def tracks(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [t.model_dump(mode="json") for t in st.list_tracks(iid)]}

    # -- alerts ---------------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/alerts")
    def alerts(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [a.model_dump(mode="json") for a in st.list_alerts(iid)]}

    @app.post("/api/incidents/{iid}/alerts", dependencies=[Depends(auth)])
    def create_alert(iid: str, body: AlertCreate) -> dict[str, Any]:
        inc = incident_or_404(iid)
        a = guard(lambda: st.create_alert(Alert(incident_id=iid, provenance=inc.provenance, **body.model_dump())))
        return a.model_dump(mode="json")

    # -- near-IR ---------------------------------------------------------------------------------------------
    from ..sources import near_ir as nir

    def _nir_source():
        src = getattr(rt.live, "url_source", None) if rt.live else None
        return src if isinstance(src, nir.NearIRVideoSource) else None

    @app.post("/api/near-ir/preview")
    async def near_ir_preview(request: Request, enabled: bool = True, denoise: bool = True, clahe: bool = True,
                              clahe_clip: float = 2.5, gamma: float = 0.8, contrast_stretch: bool = True):
        """Raw versus processed for an uploaded frame (recorded footage, any mode). Labelled image processing."""
        from fastapi.responses import Response

        data = await request.body()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        if img is None:
            raise ApiError(422, "bad_image", "send a JPEG or PNG frame as the request body")
        e = nir.Enhancement(enabled=enabled, denoise=denoise, clahe=clahe, clahe_clip=clahe_clip, gamma=gamma,
                            contrast_stretch=contrast_stretch)
        ok_, buf = cv2.imencode(".jpg", nir.side_by_side(img, e), [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), media_type="image/jpeg",
                        headers={"X-Image-Processing": ", ".join(e.steps()) or "none", "Cache-Control": "no-store"})

    @app.get("/api/sources/near-ir")
    def near_ir_status() -> dict[str, Any]:
        src = _nir_source()
        if src is None:
            return {"active": False, "note": "near-IR is a LIVE-mode source (file, webcam or stream); not thermal"}
        return {"active": True, **src.describe()}

    @app.post("/api/sources/near-ir/enhancement", dependencies=[Depends(auth)])
    def near_ir_enhancement(body: dict[str, Any]) -> dict[str, Any]:
        src = _nir_source()
        if src is None:
            raise ApiError(409, "not_active", "select the near-IR source first (LIVE backend)")
        src.enhancement = guard(lambda: nir.Enhancement(**{k: body[k] for k in body
                                                           if k in nir.Enhancement.__dataclass_fields__}))
        if svc.current_id:
            st.append_event(svc.current_id, "source_updated", {"source": "near_ir", "image_processing": src.enhancement.steps()})
        return src.describe()

    @app.get("/api/sources/near-ir/preview.jpg")
    def near_ir_live_preview():
        from fastapi.responses import Response

        src = _nir_source()
        if src is None or src.last_raw is None:
            raise ApiError(404, "not_active", "no near-IR frame yet")
        ok_, buf = cv2.imencode(".jpg", nir.side_by_side(src.last_raw, src.enhancement), [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # -- follow ---------------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/follow")
    def follow_view(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return svc.follow_view(iid)

    @app.post("/api/incidents/{iid}/follow/designate", dependencies=[Depends(auth)])
    def follow_designate(iid: str, body: FollowBody) -> dict[str, Any]:
        incident_or_404(iid)
        if not body.track_id:
            raise ApiError(422, "validation_error", "track_id is required")
        try:
            return svc.follow_designate(iid, body.track_id, body.by, body.session_id, body.designation)
        except KeyError as e:
            raise ApiError(404, "not_found", f"no such track {body.track_id}") from e
        except PermissionError as e:
            raise ApiError(403, "forbidden", str(e)) from e
        except ValueError as e:
            raise ApiError(409, "target_unavailable", str(e)) from e

    @app.post("/api/incidents/{iid}/follow/describe", dependencies=[Depends(auth)])
    def follow_describe(iid: str, body: TextIn) -> dict[str, Any]:
        """Who to follow, in words: D3 matches the description against the people in view (decisions engine or
        rules); the proposal waits for the operator's confirmation."""
        incident_or_404(iid)
        if not body.text.strip():
            raise ApiError(422, "validation_error", "describe who to follow (clothing words)")
        with rt.pipeline.lock:
            guard(lambda: svc.require_current_control(iid))
            sent = rt.pipeline.describe(body.text.strip(), {"incident_id": iid, "session_id": rt.pipeline.session_id})
        st.append_event(iid, "follow_describe", {"text": body.text.strip(), "sent": bool(sent)})
        return {"ok": bool(sent), "note": "the proposal appears for confirmation" if sent else
                "no people in view to match, or a request is already in flight"}

    def mark_call(iid, function, *args):
        incident_or_404(iid)
        try:
            return function(iid, *args)
        except (ValueError, PermissionError, KeyError) as e:
            raise ApiError(409, "mark_unavailable", str(e)) from e

    @app.get("/api/incidents/{iid}/person-mark", dependencies=[Depends(auth)])
    def person_mark(iid: str):
        return mark_call(iid, svc.person_mark.view)

    @app.post("/api/incidents/{iid}/person-mark", dependencies=[Depends(auth)])
    def person_mark_request(iid: str, body: PersonMarkIn):
        return mark_call(iid, svc.person_mark.request, body.text)

    @app.post("/api/incidents/{iid}/person-mark/confirm", dependencies=[Depends(auth)])
    def person_mark_confirm(iid: str, body: PersonMarkRef):
        return mark_call(iid, svc.person_mark.confirm, body.request_id)

    @app.post("/api/incidents/{iid}/person-mark/release", dependencies=[Depends(auth)])
    def person_mark_release(iid: str):
        return mark_call(iid, svc.person_mark.release)

    @app.post("/api/incidents/{iid}/person-mark/follow", dependencies=[Depends(auth)])
    def person_mark_follow(iid: str, body: PersonMarkRef):
        return mark_call(iid, svc.person_mark.start_follow, body.request_id)

    @app.get("/api/incidents/{iid}/person-mark/preview", dependencies=[Depends(auth)])
    def person_mark_preview(iid: str, request_id: str):
        incident_or_404(iid)
        with rt.pipeline.lock:
            state = svc.person_mark.requests.get(iid, {})
            image = svc.person_mark.previews.get(state.get("track_id"))
            if state.get("request_id") != request_id or not image:
                raise ApiError(404, "no_preview", "no preview for this request")
            return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/incidents/{iid}/person-mark/transcribe", dependencies=[Depends(auth)])
    async def person_mark_transcribe(iid: str, request: Request, format: Literal["webm", "mp4", "wav"]):
        from ..voice.transcription import TranscriptionUnavailable, transcribe
        incident_or_404(iid)
        if iid != svc.current_id:
            raise ApiError(409, "inactive_incident", "select the current incident")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise ApiError(413, "audio_too_large", "record a command under 20 seconds")
            chunks.append(chunk)
        try:
            text = await transcribe(rt.cfg, b"".join(chunks), format)
        except TranscriptionUnavailable as e:
            raise ApiError(503, "transcription_unavailable", str(e)) from e
        except ValueError as e:
            raise ApiError(422, "invalid_audio", str(e)) from e
        return {"text": text}

    @app.post("/api/incidents/{iid}/follow/describe/confirm", dependencies=[Depends(auth)])
    def follow_describe_confirm(iid: str, body: FollowBody | None = None) -> dict[str, Any]:
        incident_or_404(iid)
        guard(lambda: svc.require_current_control(iid))
        prop = rt.pipeline.describe_proposal or {}
        if prop.get("incident_id", iid) != iid or prop.get("session_id", rt.pipeline.session_id) != rt.pipeline.session_id:
            raise ApiError(409, "stale_proposal", "request a current description match")
        tid = prop.get("track_id")
        if not tid:
            raise ApiError(409, "no_proposal", "no description match to confirm")
        try:
            view = svc.follow_designate(iid, tid, (body or FollowBody()).by + " (confirmed description match)")
        except (ValueError, PermissionError, KeyError) as e:
            raise ApiError(409, "stale_proposal", str(e)) from e
        rt.pipeline.describe_proposal = None
        return view

    @app.post("/api/incidents/{iid}/look-for", dependencies=[Depends(auth)])
    def look_for(iid: str, body: TextIn) -> dict[str, Any]:
        """What to look for (clothing words): weighs pursuit routing towards matching remembered sightings.
        Empty text clears it."""
        incident_or_404(iid)
        with rt.pipeline.lock:
            guard(lambda: svc.require_current_control(iid))
            req = rt.pipeline.set_look_for(body.text or None)
        st.append_event(iid, "look_for", {"text": req["text"] if req else None})
        return {"look_for": req}

    @app.post("/api/incidents/{iid}/follow/confirm", dependencies=[Depends(auth)])
    def follow_confirm(iid: str, body: FollowBody | None = None) -> dict[str, Any]:
        incident_or_404(iid)
        body = body or FollowBody()
        return guard(lambda: svc.follow_confirm(iid, body.by, body.session_id, body.designation))

    @app.post("/api/incidents/{iid}/follow/reject", dependencies=[Depends(auth)])
    def follow_reject(iid: str, body: FollowBody | None = None) -> dict[str, Any]:
        incident_or_404(iid)
        body = body or FollowBody()
        return guard(lambda: svc.follow_reject(iid, body.by, body.session_id, body.designation))

    @app.post("/api/incidents/{iid}/follow/release", dependencies=[Depends(auth)])
    def follow_release(iid: str, body: FollowBody | None = None) -> dict[str, Any]:
        incident_or_404(iid)
        body = body or FollowBody()
        return guard(lambda: svc.follow_release(iid, body.by, body.session_id, body.designation))

    # -- recommendations (MissionActions) ---------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/actions")
    def list_actions(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [a.model_dump(mode="json") for a in st.list_actions(iid)],
                "decisions": rt.pipeline.decisions.router.status().model_dump()
                if getattr(rt.pipeline, "decisions", None) is not None else None}

    @app.post("/api/incidents/{iid}/actions", dependencies=[Depends(auth)])
    def propose_action(iid: str, body: ProposalIn) -> dict[str, Any]:
        incident_or_404(iid)
        return guard(lambda: svc.propose(iid, body.action_type, body.parameters, body.proposed_by, body.engine,
                                         body.rationale)).model_dump(mode="json")

    @app.post("/api/actions/{aid}/approve", dependencies=[Depends(auth)])
    def approve_action(aid: str, body: DecideBody | None = None) -> dict[str, Any]:
        return guard(lambda: svc.decide_action(aid, True, (body or DecideBody()).by)).model_dump(mode="json")

    @app.post("/api/actions/{aid}/reject", dependencies=[Depends(auth)])
    def reject_action(aid: str, body: DecideBody | None = None) -> dict[str, Any]:
        return guard(lambda: svc.decide_action(aid, False, (body or DecideBody()).by)).model_dump(mode="json")

    @app.post("/api/alerts/{aid}/acknowledge")
    def acknowledge(aid: str, body: AckIn | None = None) -> dict[str, Any]:
        return guard(lambda: svc.acknowledge(aid, (body or AckIn()).by)).model_dump(mode="json")

    @app.post("/api/alerts/{aid}/delivered")
    def delivered(aid: str) -> dict[str, Any]:
        return guard(lambda: st.mark_delivered(aid)).model_dump(mode="json")

    # -- responder ------------------------------------------------------------------------------------------
    @app.post("/api/incidents/{iid}/responder-position")
    def responder_position(iid: str, body: ResponderPosition) -> dict[str, Any]:
        inc = incident_or_404(iid)
        data = body.model_dump(mode="json")
        if body.source == "simulated":
            # simulator ground truth for the scripted responder (labelled SIMULATED; only in a SIMULATED backend)
            a = rt.sim.actor("responder") if rt.sim is not None else None
            if a is None:
                raise ApiError(409, "not_simulated", "simulated responder positions need the SIMULATED backend")
            data.update(enu={"frame": inc.enu_frame, "x": float(a.pos[0]), "y": float(a.pos[1]), "z": 0.0}, accuracy_m=0.5,
                        heading_deg=float(a.heading) % 360, heading_accuracy_deg=5.0, lat=None, lon=None)
        return svc.set_responder(iid, data)

    @app.get("/api/incidents/{iid}/responder-brief")
    def responder_brief(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return svc.responder_brief(iid)

    @app.get("/api/incidents/{iid}/responder-position")
    def get_responder_position(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return {"position": svc.responder.get(iid)}

    # -- scene ----------------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/scene")
    def scene(iid: str) -> dict[str, Any]:
        inc = incident_or_404(iid)
        return {"enu_frame": inc.enu_frame,
                "anchors": [a.model_dump(mode="json") for a in st.list_anchors(iid)],
                "transforms": [t.model_dump(mode="json") for t in st.list_transforms(iid)]}

    @app.post("/api/incidents/{iid}/scene/anchors", dependencies=[Depends(auth)])
    def add_anchor(iid: str, body: AnchorIn) -> dict[str, Any]:
        incident_or_404(iid)
        a = guard(lambda: st.add_anchor(SceneAnchor(incident_id=iid, coordinate_frame=body.position.frame,
                                                    **body.model_dump())))
        return a.model_dump(mode="json")

    @app.post("/api/incidents/{iid}/georef", dependencies=[Depends(auth)])
    def georef(iid: str, body: GeorefBody) -> dict[str, Any]:
        inc = incident_or_404(iid)
        ref = inc.coordinate_reference
        assert ref is not None
        prev = ref.model_dump()
        ref.origin_lat, ref.origin_lon = body.origin_lat, body.origin_lon
        ref.north_offset_deg, ref.note = body.north_offset_deg, body.note
        st.update_incident(inc, "georef_set", {"from": prev, "to": ref.model_dump()})
        return inc.model_dump(mode="json")

    @app.get("/api/observations/{oid}/location")
    def observation_location(oid: str) -> dict[str, Any]:
        from ..incident.geo import location_report

        o = guard(lambda: st.get_observation(oid))
        inc = incident_or_404(o.incident_id)
        return location_report(inc, o.world_position, o.horizontal_uncertainty_m, o.position_estimation_method)

    @app.get("/api/incidents/{iid}/structure")
    def structure(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        from ..missions.structure import register_buildings, structure_view

        register_buildings(svc, iid)
        return structure_view(svc, iid)

    @app.post("/api/incidents/{iid}/structure/side-a", dependencies=[Depends(auth)])
    def side_a(iid: str, body: SideABody) -> dict[str, Any]:
        incident_or_404(iid)
        from ..missions.structure import register_buildings, set_side_a, structure_view

        register_buildings(svc, iid)
        guard(lambda: set_side_a(svc, iid, body.building_id, body.compass, body.by))
        return structure_view(svc, iid)

    @app.post("/api/incidents/{iid}/scene/transforms", dependencies=[Depends(auth)])
    def add_transform(iid: str, body: Transform) -> dict[str, Any]:
        incident_or_404(iid)
        return guard(lambda: st.add_transform(iid, body)).model_dump(mode="json")

    # -- 3D scene assets (mesh, point cloud, splat) and overlays ------------------------------------------
    from ..config import REPO_ROOT
    from ..incident import scene as scn

    def _asset_out(a: "scn.SceneAsset") -> dict[str, Any]:
        tf = [t.model_dump(mode="json") for t in st.list_transforms(a.incident_id)
              if a.frame in (t.source_frame, t.target_frame)]
        return {**a.model_dump(mode="json"), "url": f"/api/scene-assets/{a.id}/file", "transforms": tf}

    @app.get("/api/incidents/{iid}/scene/assets")
    def list_scene_assets(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [_asset_out(a) for a in scn.list_assets(st, iid)],
                "import_format": "PLY or OBJ in metres (z up unless up_axis=y), or a .splat/.ksplat; optional "
                                 "transform.json {source_frame, target_frame, matrix 4x4, method, residual_m}"}

    @app.post("/api/incidents/{iid}/scene/assets", dependencies=[Depends(auth)])
    async def upload_scene_asset(iid: str, request: Request, name: str, format: Literal["ply", "obj", "splat", "ksplat"],
                                 origin: str = "imported file", provenance: Literal["live", "replay", "simulated", "manual"] = "manual",
                                 reconstructed: bool = False, frame_kind: Literal["scene", "arkit"] = "scene",
                                 up_axis: Literal["z", "y"] = "z") -> dict[str, Any]:
        incident_or_404(iid)
        data = await request.body()
        if len(data) > scn.MAX_ASSET_BYTES:
            raise ApiError(413, "too_large", f"scene files are limited to {scn.MAX_ASSET_BYTES // 2**20} MB")
        try:
            kind, points, faces = scn.inspect_file(data, format)
        except ValueError as e:
            raise ApiError(422, "bad_scene_file", str(e)) from e
        aid = scn.new_id("SCN")
        rel = f"{aid}.{format}"
        (scn.scenes_dir(st, REPO_ROOT) / rel).write_bytes(data)
        a = guard(lambda: scn.save_asset(st, scn.SceneAsset(
            id=aid, incident_id=iid, name=name, kind=kind, format=format, frame=f"{frame_kind}:{aid}", file=rel,  # type: ignore[arg-type]
            bytes=len(data), provenance=provenance, origin=origin, reconstructed=reconstructed, points=points,
            faces=faces, up_axis=up_axis)))
        return _asset_out(a)

    @app.post("/api/scene-assets/{aid}/transform", dependencies=[Depends(auth)])
    async def asset_transform(aid: str, request: Request) -> dict[str, Any]:
        a = guard(lambda: scn.get_asset(st, aid))
        inc = incident_or_404(a.incident_id)
        try:
            tf = scn.parse_transform_json(await request.body(), a.frame, inc.enu_frame)
        except (ValueError, KeyError) as e:
            raise ApiError(422, "bad_transform", str(e)) from e
        guard(lambda: st.add_transform(a.incident_id, tf))
        return _asset_out(a)

    @app.get("/api/scene-assets/{aid}/file")
    def asset_file(aid: str):
        from fastapi.responses import FileResponse

        a = guard(lambda: scn.get_asset(st, aid))
        f = scn.scenes_dir(st, REPO_ROOT) / a.file
        if not f.is_file():
            raise ApiError(404, "not_found", "scene file missing on disk")
        return FileResponse(f, media_type="application/octet-stream", filename=f"{a.name}.{a.format}")

    @app.post("/api/incidents/{iid}/scene/sim-export", dependencies=[Depends(auth)])
    def sim_export(iid: str) -> dict[str, Any]:
        inc = incident_or_404(iid)
        from ..sim.scenarios import get_scenario

        try:
            spec = getattr(get_scenario(inc.scenario_id), "scene", None) if inc.scenario_id else None
        except (KeyError, ValueError):
            spec = None
        if inc.provenance != "simulated" or spec is None:
            raise ApiError(409, "not_simulated", "simulator geometry exists only for a simulated incident with a scene spec")
        for a in scn.list_assets(st, iid):
            if a.origin.startswith("simulator geometry export"):
                return _asset_out(a)
        data, n = scn.simulator_point_cloud(spec)
        aid = scn.new_id("SCN")
        (scn.scenes_dir(st, REPO_ROOT) / f"{aid}.ply").write_bytes(data)
        a = scn.save_asset(st, scn.SceneAsset(id=aid, incident_id=iid, name=f"Simulator geometry: {spec.title}",
                                              kind="point_cloud", format="ply", frame=f"scene:{aid}", file=f"{aid}.ply",
                                              bytes=len(data), provenance="simulated", points=n,
                                              origin="simulator geometry export (exact, not a reconstruction)"))
        # the simulator world frame is the incident's local ground frame, so this alignment is exact by construction
        st.add_transform(iid, Transform(source_frame=a.frame, target_frame=inc.enu_frame, matrix=np.eye(4).tolist(),
                                        method="simulator (exact by construction)", residual_m=0.0))
        return _asset_out(a)

    @app.post("/api/incidents/{iid}/scene/sample-indoor", dependencies=[Depends(auth)])
    def sample_indoor(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        for a in scn.list_assets(st, iid):
            if a.origin.startswith("SYNTHETIC sample"):
                return _asset_out(a)
        data, n = scn.sample_indoor_scan()
        aid = scn.new_id("SCN")
        (scn.scenes_dir(st, REPO_ROOT) / f"{aid}.ply").write_bytes(data)
        a = scn.save_asset(st, scn.SceneAsset(id=aid, incident_id=iid, name="Sample indoor scan (synthetic)",
                                              kind="point_cloud", format="ply", frame=f"arkit:{aid}", file=f"{aid}.ply",
                                              bytes=len(data), provenance="simulated", points=n,
                                              origin="SYNTHETIC sample generated by code (not a real scan)"))
        return _asset_out(a)

    @app.get("/api/incidents/{iid}/scene/overlay")
    def scene_overlay(iid: str, frame: str) -> dict[str, Any]:
        inc = incident_or_404(iid)
        return guard(lambda: scn.overlay(st, iid, frame, inc.enu_frame))

    # -- sources --------------------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/sources")
    def sources(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        return rt.video.list()

    @app.post("/api/incidents/{iid}/sources", dependencies=[Depends(auth)])
    def select_source(iid: str, body: SourceBody) -> dict[str, Any]:
        from ..sources.video import SourceError

        incident_or_404(iid)
        prev = rt.video.active
        try:
            src = rt.video.select(body.source, body.value)
        except SourceError as e:
            st.append_event(iid, "error", {"where": "source_select", "source": body.source, "message": str(e)})
            raise ApiError(409, "source_unavailable", str(e)) from e
        st.upsert_source(iid, src, log=False)
        st.append_event(iid, "source_changed", {"from": prev, "to": body.source, "value": body.value,
                                                 "status": src.connection_status, "mode": src.source_mode})
        return src.model_dump(mode="json")

    # -- replay ---------------------------------------------------------------------------------------------
    def replay(iid: str, action: str, value: Any = None, title: str | None = None) -> dict[str, Any]:
        inc = incident_or_404(iid)
        res = guard(lambda: rt.control(action, value))
        st.append_event(iid, "replay_control", {"action": action, "value": value})
        if action == "restart" and value and str(value) != inc.scenario_id:
            # another scenario in the same world restarts in place: the incident's title and scenario follow it
            prev = (inc.title, inc.scenario_id)
            inc.scenario_id = str(value)
            if title:
                inc.title = title
            elif prev[1] and inc.title.endswith(f"· {prev[1]}"):
                inc.title = inc.title[: -len(prev[1])] + str(value)
            st.update_incident(inc, "incident_retitled", {"from": {"title": prev[0], "scenario_id": prev[1]},
                                                         "to": {"title": inc.title, "scenario_id": inc.scenario_id}})
        return res

    @app.post("/api/incidents/{iid}/replay/start", dependencies=[Depends(auth)])
    def replay_start(iid: str) -> dict[str, Any]:
        return replay(iid, "resume")

    @app.post("/api/incidents/{iid}/replay/pause", dependencies=[Depends(auth)])
    def replay_pause(iid: str) -> dict[str, Any]:
        return replay(iid, "pause")

    @app.post("/api/incidents/{iid}/replay/seek", dependencies=[Depends(auth)])
    def replay_seek(iid: str, body: ReplayBody) -> dict[str, Any]:
        return replay(iid, "seek", body.value)

    @app.post("/api/incidents/{iid}/replay/restart", dependencies=[Depends(auth)])
    def replay_restart(iid: str, body: ReplayBody | None = None) -> dict[str, Any]:
        return replay(iid, "restart", body.value if body else None, body.title if body else None)

    # -- events and metrics ---------------------------------------------------------------------------------
    @app.get("/api/incidents/{iid}/events")
    def events(iid: str, after: int = 0, limit: int = Query(2000, le=10000)) -> dict[str, Any]:
        incident_or_404(iid)
        return {"items": [e.model_dump(mode="json") for e in st.events(iid, after, limit)]}

    @app.get("/api/incidents/{iid}/metrics")
    def metrics(iid: str) -> dict[str, Any]:
        incident_or_404(iid)
        from ..incident.metrics import compute_metrics
        return compute_metrics(st, iid)

    # -- stream ---------------------------------------------------------------------------------------------
    @app.websocket("/ws/incidents/{iid}")
    async def ws_incident(ws: WebSocket, iid: str, after: int = 0) -> None:
        try:
            st.get_incident(iid)
        except NotFound:
            await ws.close(code=4404)
            return
        await ws.accept()
        q = svc.subscribe(iid)
        try:
            backlog = st.events(iid, after)
            await ws.send_text(dumps({"type": "backlog", "items": [e.model_dump(mode="json") for e in backlog]}).decode())
            last = backlog[-1].seq if backlog else after

            async def reader() -> None:
                while True:
                    m = await ws.receive()
                    if m.get("type") == "websocket.disconnect":
                        return

            rtask = asyncio.create_task(reader())
            try:
                while not rtask.done():
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if msg["seq"] <= last:
                        continue  # already sent in the backlog
                    last = msg["seq"]
                    await ws.send_text(dumps(msg).decode())
            finally:
                rtask.cancel()
        except Exception:  # noqa: BLE001  client went away
            pass
        finally:
            svc.unsubscribe(iid, q)
