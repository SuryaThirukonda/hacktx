"""What the responder phone may say about each confirmed target, computed server-side so every number is traceable.

Rules (from the brief):
* range and bearing only when both the target and the responder have valid positions in the same frame;
* a clock direction only when the responder's heading is present and trustworthy (accuracy <= 20 deg);
  otherwise a cardinal direction, or "refer to the map";
* never an exact distance without its uncertainty; never temperatures, life signs, safe-entry or threat claims.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .models import ObservationView, Position, utcnow
from .store import IncidentStore

TRUSTED_HEADING_DEG = 20.0
CARDINALS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def cardinal(bearing_deg: float) -> str:
    return CARDINALS[int(((bearing_deg % 360) + 22.5) // 45) % 8]


def clock_of(rel_deg: float) -> int:
    c = round((rel_deg % 360) / 30.0) % 12
    return 12 if c == 0 else c


def enu_of_responder(pos: dict[str, Any] | None, enu_frame: str, origin: tuple[float, float] | None,
                     north_offset: float = 0.0) -> tuple[Position | None, float | None, str]:
    """(responder ENU position, its horizontal accuracy, how it was obtained)."""
    if not pos:
        return None, None, "no responder position"
    if pos.get("enu"):
        p = Position.model_validate(pos["enu"])
        if p.frame != enu_frame:
            return None, None, f"responder position is in {p.frame}, not {enu_frame}"
        return p, pos.get("accuracy_m"), pos.get("source", "enu")
    if pos.get("lat") is not None and pos.get("lon") is not None:
        if origin is None:
            return None, pos.get("accuracy_m"), "incident has no geographic origin: GPS cannot be placed on the incident map"
        from types import SimpleNamespace

        from ..geometry.geo import latlon_to_local

        x, y = latlon_to_local(float(pos["lat"]), float(pos["lon"]), SimpleNamespace(lat=origin[0], lon=origin[1]),
                               north_offset)
        return Position(frame=enu_frame, x=x, y=y), pos.get("accuracy_m"), "browser_geolocation"
    return None, None, "no usable responder position"


def responder_targets(st: IncidentStore, incident_id: str, responder: dict[str, Any] | None,
                      now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    inc = st.get_incident(incident_id)
    ref = inc.coordinate_reference
    origin = (ref.origin_lat, ref.origin_lon) if ref and ref.origin_lat is not None and ref.origin_lon is not None else None
    if responder and responder.get("source") == "browser_geolocation" and inc.provenance == "simulated":
        # a real GPS fix has no meaning inside a simulated world, even though the simulator has a stand-in origin
        rpos, racc, how = None, responder.get("accuracy_m"), "a real GPS fix cannot be placed in a simulated incident"
    else:
        rpos, racc, how = enu_of_responder(responder, inc.enu_frame, origin, ref.north_offset_deg if ref else 0.0)
    heading = responder.get("heading_deg") if responder else None
    hacc = responder.get("heading_accuracy_deg") if responder else None
    heading_ok = heading is not None and hacc is not None and float(hacc) <= TRUSTED_HEADING_DEG

    targets = []
    for v in st.list_observations(incident_id, status="confirmed", now=now) + [
            v for v in st.list_observations(incident_id, now=now) if v.observation.provenance == "manual"]:
        targets.append(_target(v, rpos, racc, heading if heading_ok else None, now))
    return {"incident_id": incident_id, "mission": inc.mission_mode, "enu_frame": inc.enu_frame,
            "responder": {"grounded": rpos is not None, "how": how, "accuracy_m": racc,
                          "heading_deg": heading, "heading_trusted": heading_ok},
            "targets": targets}


def _target(v: ObservationView, rpos: Position | None, racc: float | None, heading: float | None,
            now: datetime) -> dict[str, Any]:
    o = v.observation
    age = max(0.0, (now - o.observed_at).total_seconds())
    out: dict[str, Any] = {"observation_id": o.id, "label": o.label, "category": o.category,
                           "provenance": o.provenance, "review_status": v.review_status,
                           "last_observed_s": round(age), "stale": v.stale,
                           "side": o.attributes.get("side"), "grounded": False}
    if o.world_position is None:
        side = f", side {o.attributes['side']}" if o.attributes.get("side") else ""
        if o.provenance == "manual":
            out["text"] = f"{_cap(o.label)}{side}, marked by the operator. No map position."
        else:
            out["text"] = f"{_cap(o.label)}{side}: position unavailable. Refer to video. Last observed {round(age)} seconds ago."
        return out
    if rpos is None or rpos.frame != o.world_position.frame:
        out["text"] = f"{_cap(o.label)} is on the map. Your position is not available, so no bearing or distance."
        return out
    dx, dy = o.world_position.x - rpos.x, o.world_position.y - rpos.y
    rng = math.hypot(dx, dy)
    brg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    unc = math.hypot(o.horizontal_uncertainty_m or 0.0, racc or 0.0)
    out.update(grounded=True, range_m=round(rng, 1), bearing_deg=round(brg), uncertainty_m=round(unc, 1),
               cardinal=cardinal(brg))
    if heading is not None:
        out["clock"] = clock_of(brg - heading)
        where = f"{out['clock']} o'clock"
    else:
        where = f"to the {out['cardinal']}"
    out["text"] = (f"{_cap(o.label)}, {where}, about {round(rng)} metres, plus or minus {unc:.0f}. "
                   f"Last observed {round(age)} seconds ago.")
    return out


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
