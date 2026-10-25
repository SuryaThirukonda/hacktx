"""Rebuild an incident's records from its event log alone.

The event log is the incident's source of truth: every record the metrics read is either carried whole in an event
(observations, reviews, alerts, anchors, transforms, actions) or reconstructed from event fields (alert delivery and
acknowledgment times, track starts and losses). ``rebuild`` replays the log into a fresh in-memory store, so metrics
recomputed from the log can be compared with the live values.
"""

from __future__ import annotations

from typing import Iterable

from .models import Alert, IncidentEvent, MissionAction, Observation, Review, SceneAnchor, Track, Transform
from .store import IncidentStore


def rebuild(events: Iterable[IncidentEvent]) -> IncidentStore:
    st = IncidentStore(":memory:")
    ts = st._ts  # noqa: SLF001
    for e in sorted(events, key=lambda x: x.seq):
        p = e.payload
        k = e.kind
        if k == "observation":
            o = Observation.model_validate(p["observation"])
            st._put("observations_raw", {"id": o.id, "incident_id": o.incident_id, "observed_at": ts(o.observed_at)}, o)  # noqa: SLF001
        elif k == "review":
            r = Review.model_validate(p)
            st._put("reviews", {"id": r.id, "observation_id": r.observation_id, "at": ts(r.at)}, r)  # noqa: SLF001
        elif k == "alert":
            a = Alert.model_validate(p)
            st._put("alerts", {"id": a.id, "incident_id": a.incident_id, "created_at": ts(a.created_at)}, a)  # noqa: SLF001
        elif k in ("alert_delivered", "alert_acknowledged"):
            data = st.get_alert(p["alert_id"]).model_dump(mode="json")
            at = p.get("at") or e.at.isoformat()
            if k == "alert_delivered":
                data["delivered_at"] = at
                data["status"] = "delivered" if data["status"] == "pending" else data["status"]
            else:
                data.update(acknowledged_at=at, acknowledged_by=p.get("by"), status="acknowledged")
            a = Alert.model_validate(data)
            st._put("alerts", {"id": a.id, "incident_id": a.incident_id, "created_at": ts(a.created_at)}, a)  # noqa: SLF001
        elif k.startswith("track_"):
            try:
                t = st.get_track(p["track_id"])
                t.tracking_status = p.get("status", t.tracking_status)
                t.last_seen = e.at
            except KeyError:
                t = Track(id=p["track_id"], incident_id=e.incident_id, first_seen=p.get("first_seen") or e.at,  # type: ignore[arg-type]
                          last_seen=e.at, provenance=p.get("provenance", "simulated"))
            st._put("tracks", {"id": t.id, "incident_id": t.incident_id}, t)  # noqa: SLF001
        elif k == "anchor":
            a2 = SceneAnchor.model_validate(p)
            st._put("anchors", {"id": a2.id, "incident_id": a2.incident_id}, a2)  # noqa: SLF001
        elif k == "transform":
            tf = Transform.model_validate(p)
            st._put("transforms", {"id": tf.id, "incident_id": e.incident_id}, tf)  # noqa: SLF001
        elif k == "decision_proposal":
            act = MissionAction.model_validate(p)
            st._put("actions", {"id": act.id, "incident_id": act.incident_id, "created_at": ts(act.created_at)}, act)  # noqa: SLF001
        # copy the event itself so event-based metrics see the same log
        st._append_raw(e)  # noqa: SLF001
    return st
