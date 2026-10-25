"""Incident metrics computed only from the persisted event log and records (never invented).

Every value carries what it was computed from; durations are only reported when both ends were logged.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Any

from .store import IncidentStore


def _dur(a: str | datetime | None, b: str | datetime | None) -> float | None:
    if a is None or b is None:
        return None
    a = datetime.fromisoformat(a) if isinstance(a, str) else a
    b = datetime.fromisoformat(b) if isinstance(b, str) else b
    return (b - a).total_seconds()


def _summ(xs: list[float]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None and x >= 0]
    return {"n": len(xs), "median_s": round(median(xs), 3) if xs else None, "max_s": round(max(xs), 3) if xs else None}


def compute_metrics(st: IncidentStore, incident_id: str) -> dict[str, Any]:
    views = st.list_observations(incident_id)
    obs = {v.observation.id: v for v in views}
    events = st.events(incident_id, limit=1_000_000_000)  # the whole log, however long the incident
    candidates = [v for v in views if v.observation.category == "person"]

    # time from detection to the first review of that observation
    first_review: dict[str, datetime] = {}
    for e in events:
        if e.kind == "review":
            oid = e.payload.get("observation_id")
            if oid and oid not in first_review:
                first_review[oid] = e.at
    review_lat = [_dur(obs[o].observation.received_at, t) for o, t in first_review.items() if o in obs]

    # time from confirmation to an alert that cites the observation
    confirm_at = {o: t for o, t in first_review.items()
                  if o in obs and obs[o].review_status == "confirmed"}
    alerts = st.list_alerts(incident_id)
    conf_to_alert = []
    for a in alerts:
        ts = [confirm_at[o] for o in a.source_observation_ids if o in confirm_at]
        if ts:
            conf_to_alert.append(_dur(min(ts), a.created_at))
    ack_lat = [_dur(a.delivered_at or a.created_at, a.acknowledged_at) for a in alerts if a.acknowledged_at]

    tracks = st.list_tracks(incident_id)
    losses = sum(1 for e in events if e.kind == "track_lost")
    with_pos = sum(1 for v in views if v.observation.world_position is not None)

    # localization error only where ground truth exists (simulated observations matched to a simulator actor)
    loc = []
    for v in views:
        o = v.observation
        truth = o.attributes.get("sim_truth")
        if o.world_position is not None and truth and truth.get("xy"):
            loc.append(math.hypot(o.world_position.x - truth["xy"][0], o.world_position.y - truth["xy"][1]))
    localization = ({"n": len(loc), "median_m": round(median(loc), 3), "max_m": round(max(loc), 3),
                     "source": "simulator ground truth, matched by image box overlap (evaluation only)"}
                    if loc else None)

    return {
        "incident_id": incident_id,
        "computed_from": "incident event log and stored records",
        "observations": {"total": len(views), "with_valid_world_position": with_pos,
                         "without_world_position": len(views) - with_pos,
                         "by_provenance": _count(v.observation.provenance for v in views)},
        "candidates": {"total": len(candidates),
                       "confirmed": sum(1 for v in candidates if v.review_status == "confirmed"),
                       "rejected": sum(1 for v in candidates if v.review_status == "rejected"),
                       "pending": sum(1 for v in candidates if v.review_status == "pending")},
        "detection_to_review": _summ(review_lat),
        "confirmation_to_alert": _summ(conf_to_alert),
        "alert_ack_latency": _summ(ack_lat),
        "alerts": {"total": len(alerts), "acknowledged": sum(1 for a in alerts if a.acknowledged_at)},
        "tracks": {"total": len(tracks), "loss_events": losses},
        "localization_error": localization,
        "localization_error_note": None if loc else "no observation has both a verified position and ground truth",
        "events": len(events),
        "sources": {
            "observations": "observations_raw table (every observation is also an `observation` event)",
            "candidates": "person observations and their latest `review` events",
            "detection_to_review": "observation received_at to its first `review` event",
            "confirmation_to_alert": "first confirming `review` event to the first `alert` citing the observation",
            "alert_ack_latency": "`alert_delivered` (or alert creation) to `alert_acknowledged`",
            "tracks": "`track_started` and `track_lost` events",
            "localization_error": "observation world position against simulator ground truth (simulated only)",
        },
    }


def _count(xs) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out
