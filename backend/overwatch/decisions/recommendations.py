"""Deterministic recommendation layer: the only things software (rules or a model) may recommend.

* The eight allowed recommendations are ``MissionAction`` records with who proposed them, an approval status and
  safety constraints. Their messages come from fixed templates; every number in a message is a field computed by code
  (ages from timestamps, uncertainties from the observation), never a number a model wrote.
* A model proposal names a recommendation, the record it refers to and a reason code. It passes schema validation,
  mission rules and data-availability checks, and recommendations with an effect wait for a person's approval.
  Proposals that touch flight control, carry measurements or free-text claims are rejected and logged.
* Rules run every mission cycle and propose the same records deterministically (deduplicated by key).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..incident.models import Incident, MissionAction, ObservationView, utcnow
from ..incident.store import NotFound

if TYPE_CHECKING:
    from ..incident.store import IncidentStore


@dataclass(frozen=True)
class Spec:
    template: str
    approval: bool  # True: nothing happens until a person approves
    constraints: tuple[str, ...]
    missions: tuple[str, ...] | None = None  # missions where it is allowed (None: all)


SPECS: dict[str, Spec] = {
    "review_candidate": Spec("Review {label}: observed at {observed_utc} UTC, {where}.", False,
                             ("a candidate is not a confirmation",)),
    "inspect_area": Spec("Inspect the area around {label} ({where}) before deciding.", True,
                         ("the operator flies or approves; real flight stays manual",
                          "no model output reaches flight control")),
    "mark_last_seen": Spec("Mark last seen for {label}: at {observed_utc} UTC, {where}.", False,
                           ("last seen is not a current position",), ("search", "follow")),
    "request_operator_confirmation": Spec("Operator: {question}.", False, ("nothing proceeds until a person answers",)),
    "notify_responder": Spec("Send to the responder: {brief}", True,
                             ("only confirmed observations are sent", "the responder acknowledges")),
    "show_structure_side": Spec("Show Side {side} of {building}: {count} item(s) tagged.", False,
                                ("nothing here declares an entry safe or a structure stable",), ("structure",)),
    "hold_for_more_information": Spec("Hold for more information: {why}.", False, ()),
    "return_to_operator_review": Spec("Return to operator review: {why}.", False, ()),
}

# Free text is never accepted from a model: reasons and questions are codes with fixed wording.
REASONS = {
    "positions_unverified": "positions are not verified (no valid ground calibration)",
    "stale_observation": "the observation is stale",
    "below_gate": "the decision was below its confidence gate",
    "conflicting_sightings": "sightings of the same area disagree",
    "source_offline": "the video source is offline",
}
QUESTIONS = {
    "set_side_a": "set side A of {building} so {count} opening(s) can be organised by side",
    "confirm_candidate": "confirm or reject {label}",
}
PARAM_KEYS = {"observation_id", "building_id", "side", "reason", "question"}
FLIGHT_WORDS = ("velocity", "setpoint", "motor", "throttle", "waypoint", "goto", "go_to", "takeoff", "take_off", "land",
                "arm", "pursue", "pursuit", "chase", "enter", "breach", "rtl", "yaw", "pitch", "roll", "altitude")


class ProposalRejected(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------------------------------------------------------
def _words(s: str) -> set[str]:
    return set(re.split(r"[^a-z]+", s.lower()))


def _flight_terms(v: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(v, dict):
        for k, x in v.items():
            hits += [w for w in FLIGHT_WORDS if w in _words(str(k))] + _flight_terms(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            hits += _flight_terms(x)
    elif isinstance(v, str):
        hits += [w for w in FLIGHT_WORDS if w in _words(v)]
    return hits


def _numbers_in(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, dict):
        return any(_numbers_in(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_numbers_in(x) for x in v)
    return False


def _view(store: "IncidentStore", inc: Incident, oid: str, now: datetime) -> ObservationView:
    try:
        obs = store.get_observation(oid)
    except NotFound:
        raise ProposalRejected("missing_data", f"observation {oid} does not exist") from None
    if obs.incident_id != inc.id:
        raise ProposalRejected("missing_data", f"observation {oid} belongs to another incident")
    return store.view(obs, now)


def _buildings(store: "IncidentStore", incident_id: str) -> dict[str, Any]:
    return {a.attributes.get("building_id"): a for a in store.list_anchors(incident_id)
            if a.type == "landmark" and a.attributes.get("kind") == "building"}


def _openings(store: "IncidentStore", incident_id: str, building_id: str) -> list[ObservationView]:
    return [v for v in store.list_observations(incident_id) if v.observation.category in ("opening", "hazard")
            and v.observation.attributes.get("building") in (None, building_id) and v.review_status != "rejected"]


def _side_of(anchor: Any, v: ObservationView) -> str | None:
    from ..missions.structure import _side

    o = v.observation
    if o.provenance == "manual":
        return o.attributes.get("side")
    if o.world_position is None:
        return None
    n = o.attributes.get("normal", [0, 0])
    return _side(anchor, o.world_position.x + n[0], o.world_position.y + n[1])


def facts_for(store: "IncidentStore", inc: Incident, kind: str, params: dict[str, Any], now: datetime | None = None,
              brief: str | None = None) -> dict[str, Any]:
    """Every value a template may show, computed by code from stored records."""
    now = now or utcnow()
    f: dict[str, Any] = {}
    if params.get("observation_id"):
        v = _view(store, inc, params["observation_id"], now)
        o = v.observation
        f.update(label=o.label, age_s=round(max(0.0, (now - o.observed_at).total_seconds()), 1),
                 review_status=v.review_status, stale=v.stale, verified=o.world_position is not None,
                 uncertainty_m=o.horizontal_uncertainty_m, observation_id=o.id,
                 observed_utc=o.observed_at.strftime("%H:%M:%S"))  # a time, not an age: messages never go stale
        f["where"] = (f"position within {o.horizontal_uncertainty_m:.1f} m" if o.world_position is not None
                      and o.horizontal_uncertainty_m is not None else "position not verified")
    if params.get("building_id"):
        b = _buildings(store, inc.id).get(params["building_id"])
        if b is None:
            raise ProposalRejected("missing_data", f"building {params['building_id']} is not registered")
        f.update(building=b.name, side_a=b.attributes.get("side_a"))
        ops = _openings(store, inc.id, params["building_id"])
        f["count"] = len(ops)
        if kind == "show_structure_side" and params.get("side"):
            f["count"] = sum(1 for v in ops if _side_of(b, v) == params["side"])
    if params.get("side"):
        f["side"] = params["side"]
    if params.get("reason"):
        f["why"] = REASONS[params["reason"]]
    if params.get("question"):
        f["question"] = QUESTIONS[params["question"]].format(**{"building": f.get("building", "the building"),
                                                                 "count": f.get("count", 0),
                                                                 "label": f.get("label", "the candidate")})
    if kind == "notify_responder":
        f["brief"] = brief if brief is not None else \
            f"the brief for {f.get('label', 'the observation')} ({f.get('where', '')}), worded from the responder's position when sent."
    return f


def render(kind: str, facts: dict[str, Any]) -> str:
    return SPECS[kind].template.format(**facts)


def action_key(kind: str, params: dict[str, Any]) -> str:
    target = params.get("observation_id") or params.get("building_id") or params.get("reason") or params.get("question")
    extra = f":{params['side']}" if params.get("side") else ""
    return f"{kind}:{target}{extra}"


def _open_keys(store: "IncidentStore", incident_id: str) -> set[str]:
    return {a.parameters.get("key") for a in store.list_actions(incident_id)}


# ---------------------------------------------------------------------------------------------------------------
def validate_proposal(store: "IncidentStore", inc: Incident, kind: str, params: dict[str, Any], proposed_by: str,
                      engine: str | None = None, rationale: str | None = None, now: datetime | None = None,
                      brief: str | None = None) -> MissionAction:
    """Schema, safety, mission rules and data availability. Raises ``ProposalRejected``; never guesses."""
    now = now or utcnow()
    if kind not in SPECS:
        raise ProposalRejected("not_allowed", f"{kind!r} is not an allowed recommendation (allowed: {', '.join(SPECS)})")
    hits = _flight_terms({kind: params, "rationale": rationale or ""})
    if hits:
        raise ProposalRejected("flight_control", f"no model output reaches flight control (found {sorted(set(hits))})")
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise ProposalRejected("schema", f"unknown parameters {sorted(unknown)}")
    if proposed_by == "model" and (_numbers_in(params) or any(ch.isdigit() for ch in (rationale or ""))):
        raise ProposalRejected("model_measurement", "measurements come from the system, not from a model")
    spec = SPECS[kind]
    if spec.missions is not None and inc.mission_mode not in spec.missions:
        raise ProposalRejected("mission_rule", f"{kind} is not used in {inc.mission_mode} mode")
    if params.get("reason") is not None and params["reason"] not in REASONS:
        raise ProposalRejected("schema", f"unknown reason code {params['reason']!r}")
    if params.get("question") is not None and params["question"] not in QUESTIONS:
        raise ProposalRejected("schema", f"unknown question code {params['question']!r}")
    if params.get("side") is not None and params["side"] not in ("A", "B", "C", "D"):
        raise ProposalRejected("schema", "side must be A, B, C or D")
    needs_obs = kind in ("review_candidate", "inspect_area", "mark_last_seen", "notify_responder")
    if needs_obs and not params.get("observation_id"):
        raise ProposalRejected("missing_data", f"{kind} needs an observation_id")
    if kind in ("hold_for_more_information", "return_to_operator_review") and not params.get("reason"):
        raise ProposalRejected("missing_data", f"{kind} needs a reason code")
    if kind == "request_operator_confirmation" and not params.get("question"):
        raise ProposalRejected("missing_data", "request_operator_confirmation needs a question code")
    if kind == "show_structure_side" and not (params.get("building_id") and params.get("side")):
        raise ProposalRejected("missing_data", "show_structure_side needs building_id and side")
    facts = facts_for(store, inc, kind, params, now, brief)
    if kind == "notify_responder":
        if facts["review_status"] != "confirmed":
            raise ProposalRejected("missing_data", "only confirmed observations are sent to the responder")
        if facts["stale"]:
            raise ProposalRejected("missing_data", "the observation is stale; review it again first")
    if kind == "review_candidate" and facts["review_status"] != "pending":
        raise ProposalRejected("missing_data", "the observation was already reviewed")
    if kind == "show_structure_side" and not facts.get("side_a"):
        raise ProposalRejected("missing_data", "side A is not set for this building")
    key = action_key(kind, params)
    if key in _open_keys(store, inc.id):
        raise ProposalRejected("duplicate", f"an action {key} already exists")
    needs_approval = spec.approval
    return MissionAction(
        incident_id=inc.id, mission=inc.mission_mode, action_type=kind, proposed_by=proposed_by,  # type: ignore[arg-type]
        parameters={**params, "key": key, "message": render(kind, facts), "facts": facts, "engine": engine,
                    "model_note": rationale if proposed_by == "model" else None},
        approval_status="pending" if needs_approval else "not_required", safety_constraints=list(spec.constraints),
        reason=f"proposed by {engine or proposed_by}" if proposed_by == "model" else "deterministic rule")


def propose(store: "IncidentStore", inc: Incident, kind: str, params: dict[str, Any], proposed_by: str, **kw: Any) -> MissionAction:
    """Validate and record. Rejections are logged as events so the audit shows what a model tried."""
    try:
        act = validate_proposal(store, inc, kind, params, proposed_by, **kw)
    except ProposalRejected as e:
        store.append_event(inc.id, "proposal_rejected", {"action_type": kind, "parameters": params,
                                                         "proposed_by": proposed_by, "engine": kw.get("engine"),
                                                         "code": e.code, "reason": e.reason})
        raise
    return store.add_action(act)


# ---------------------------------------------------------------------------------------------------------------
def deterministic(store: "IncidentStore", inc: Incident, calibration_valid: bool, now: datetime | None = None) -> list[MissionAction]:
    """Rules: propose what the current records call for, once per key; close actions whose condition is met."""
    now = now or utcnow()
    views = store.list_observations(inc.id, now=now)
    by_id = {v.observation.id: v for v in views}
    alerted = {oid for a in store.list_alerts(inc.id) for oid in a.source_observation_ids}
    for act in store.list_actions(inc.id):
        if act.execution_status != "not_started" or act.approval_status == "rejected":
            continue
        v = by_id.get(act.parameters.get("observation_id", ""))
        done = (act.action_type == "review_candidate" and v is not None and v.review_status != "pending") or \
               (act.action_type == "notify_responder" and v is not None and v.observation.id in alerted)
        if done:
            act.execution_status = "done"
            store.update_action(act, "action_done")
    keys = _open_keys(store, inc.id)
    wanted: list[tuple[str, dict[str, Any]]] = []
    for v in views:
        o = v.observation
        if o.provenance == "manual" or v.stale:
            continue
        if v.review_status == "pending" and not o.attributes.get("group"):  # re-sightings fold into their group
            wanted.append(("review_candidate", {"observation_id": o.id}))
        elif v.review_status == "confirmed" and o.id not in alerted:
            wanted.append(("notify_responder", {"observation_id": o.id}))
    pending = [v for v in views if v.review_status == "pending" and v.observation.provenance != "manual" and not v.stale]
    if pending and not calibration_valid and not any(v.observation.world_position for v in pending):
        wanted.append(("hold_for_more_information", {"reason": "positions_unverified"}))
    if inc.mission_mode == "structure":
        for bid, b in _buildings(store, inc.id).items():
            if not b.attributes.get("side_a") and _openings(store, inc.id, bid):
                wanted.append(("request_operator_confirmation", {"question": "set_side_a", "building_id": bid}))
    out = []
    for kind, params in wanted:
        if action_key(kind, params) in keys:
            continue
        try:
            out.append(propose(store, inc, kind, params, "rules", now=now))
        except ProposalRejected:
            continue
        keys.add(action_key(kind, params))
    return out


# Real-data mode: what each simulation decision task may become (never a flight command).
DECISION_TO_RECOMMENDATION = {
    "D6_obstacle": "hold_for_more_information", "D7_lead_triage": "review_candidate", "D8_next_sector": "inspect_area",
    "D9_route": "request_operator_confirmation", "D10_dispatch": "request_operator_confirmation",
    "D11_opening": "show_structure_side",
    "D12_pursuit_route": "inspect_area",  # a real aircraft: the chosen route is shown to the pilot, never flown
}
