"""Small incident API extension; all state belongs to IncidentService and existing records."""
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..incident.models import Review
from ..incident.store import NotFound


class SearchRequest(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    torso_color: str | None = None
    lower_color: str | None = None


class CandidateConfirmation(BaseModel):
    request_id: str
    track_id: str | None = None


class AssistanceReview(BaseModel):
    status: Literal["confirmed", "rejected"]
    reviewer: str = "operator"
    dispatch: bool = False
    session_id: str


def register(app, rt, auth):
    svc = rt.incidents

    def guarded(fn):
        try:
            return fn()
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except (ValueError, PermissionError) as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/incidents/{iid}/target")
    def target(iid: str):
        guarded(lambda: svc.store.get_incident(iid))
        return svc.targets.view(iid)

    @app.post("/api/incidents/{iid}/target/search", dependencies=[Depends(auth)])
    def search(iid: str, body: SearchRequest):
        from ..incident.person_mark import COLORS
        colors = set(COLORS.split("|"))
        if any(v and v not in colors for v in (body.torso_color, body.lower_color)):
            raise HTTPException(422, "use a supported clothing color")
        description = body.description or " and ".join(
            text for text in (f"{body.torso_color} shirt" if body.torso_color else "",
                              f"{body.lower_color} pants" if body.lower_color else "") if text)
        return guarded(lambda: svc.person_mark.request_muse(iid, description))

    @app.post("/api/incidents/{iid}/target/confirm", dependencies=[Depends(auth)])
    def confirm(iid: str, body: CandidateConfirmation):
        return guarded(lambda: svc.person_mark.confirm(iid, body.request_id, body.track_id))

    @app.get("/api/incidents/{iid}/sar")
    def assistance(iid: str):
        guarded(lambda: svc.store.get_incident(iid))
        return svc.targets.sar.view(iid)

    @app.post("/api/incidents/{iid}/sar/{oid}/review", dependencies=[Depends(auth)])
    def review(iid: str, oid: str, body: AssistanceReview):
        def run():
            with rt.pipeline.lock:
                svc.require_current_control(iid, body.session_id)
                obs = svc.store.get_observation(oid)
                if obs.incident_id != iid or not obs.attributes.get("sar"):
                    raise ValueError("not an assistance observation of this incident")
                if rt.mode != "SIMULATED" or obs.provenance != "simulated":
                    raise ValueError("assistance dispatch is simulation-only")
                if obs.attributes.get("session_id") != rt.pipeline.session_id:
                    raise ValueError("observation belongs to an earlier session")
                if body.status == "confirmed":
                    age = rt.pipeline._age("drone", rt.clock.now_ms())
                    capture = rt.pipeline.counters["drone"].last_capture_ms
                    if (age is None or age > rt.cfg.sources.drone.max_age_ms or obs.is_stale()
                            or capture is None or not -100 <= rt.clock.now_ms()-capture <= rt.cfg.sources.drone.max_age_ms):
                        raise ValueError("fresh observation/video required before confirmation/dispatch")
                current = svc.store.view(obs)
                if current.review_status != body.status:
                    svc.store.review(Review(observation_id=oid, status=body.status, reviewer=body.reviewer,
                                           note="SAR assistance review; not a medical or real-world identity finding"))
                    svc.store.append_event(iid, "sar_reviewed", {"observation_id": oid, "status": body.status,
                                                               "reviewer": body.reviewer})
                if body.status == "confirmed":
                    action = next((a for a in svc.store.list_actions(iid) if a.action_type == "notify_responder"
                                   and a.parameters.get("observation_id") == oid
                                   and a.approval_status != "rejected"), None)
                    if action is None:
                        action = svc.propose(iid, "notify_responder", {"observation_id": oid}, "rules")
                    if body.dispatch and action.approval_status == "pending":
                        svc.decide_action(action.id, True, body.reviewer)
                rescue = rt.pipeline.rescue
                if rescue.active() and rescue.beacon and rescue.beacon.get("observation_id") == oid:
                    if body.status == "confirmed" and body.dispatch and rescue.phase == "REVIEW":
                        rescue.confirm(body.reviewer)
                    elif body.status == "rejected" and rescue.phase == "REVIEW":
                        rescue.reject(body.reviewer)
                return svc.targets.sar.view(iid)
        return guarded(run)
