"""Search mission: flag possible people as candidates for human review.

* One candidate per remembered person (the object memory, ``tracking/memory.py``), never per frame: a person who
  comes back under a new tracker id after an explained absence is not flagged again.
* A new track whose verified position falls within the combined uncertainty (plus a margin) of a recent candidate
  is recorded as a re-sighting of that candidate (``attributes.group``), not as a new person. Without verified
  positions nothing is grouped: two image-only sightings may be two people.
* Tracks the operator locked as the responder are not flagged.
* Nothing here declares an area clear: the absence of a detection is not evidence of absence.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

from ..incident.models import utcnow

if TYPE_CHECKING:
    from ..incident.service import IncidentService

GROUP_MARGIN_M = 1.5
GROUP_WINDOW_S = 90.0


def auto_flag(svc: "IncidentService", incident_id: str) -> list[str]:
    rt = svc.rt
    p = rt.pipeline
    responder = p.locks["responder"].track_id if p.locks["responder"].active else None
    flagged = svc.flagged_tracks.setdefault(incident_id, set())
    created: list[str] = []
    now = utcnow()
    recent = [v for v in svc.store.list_observations(incident_id)
              if v.observation.category == "person" and v.observation.provenance != "manual"
              and now - v.observation.observed_at < timedelta(seconds=GROUP_WINDOW_S)
              and not v.observation.attributes.get("group")]
    for t in p.tracks_snapshot(rt.clock.now_ms()):
        key = f"{rt.session_id}.{t.track_id}"
        if t.class_name != "person" or t.state != "CONFIRMED" or key in flagged or t.track_id == responder:
            continue
        # object memory: a person who came back under a new track id (continued through an explained absence) is
        # the same candidate; a "possible" re-attachment still gets its own candidate (grouped below by position)
        with p.lock:
            obj = p.memory.object_for_track(t.track_id)
            okey = f"{rt.session_id}.{obj.oid}" if obj is not None and obj.link != "possible" else None
            oid = obj.oid if obj is not None else None
        flagged.add(key)
        if okey is not None and okey in flagged:
            continue
        if okey is not None:
            flagged.add(okey)
        group = None
        if t.ground_xy_m is not None and t.ground_error_m is not None:
            for v in recent:
                o = v.observation
                if o.world_position is None or o.horizontal_uncertainty_m is None or v.review_status == "rejected":
                    continue
                d = math.hypot(o.world_position.x - t.ground_xy_m[0], o.world_position.y - t.ground_xy_m[1])
                if d <= o.horizontal_uncertainty_m + t.ground_error_m + GROUP_MARGIN_M:
                    group = o.id
                    break
        attrs = {"group": group} if group else {}
        if oid is not None:
            attrs["object_id"] = oid
        obs = svc.observation_from_track(incident_id, t.track_id, "person", "possible person",
                                         flagged_by="search_rules", attributes=attrs)
        created.append(obs.id)
        if group is None:
            recent.append(svc.store.view(obs))
    return created
