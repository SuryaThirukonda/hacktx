"""Structure mission: openings and hazards organised by building side.

* Buildings come from map data. In the simulator that is the scene spec's building footprint (the same role a real
  incident would give a map or pre-incident plan); it is registered as a scene anchor and labelled as map data.
* Side A is the designated address/front side and is set by the operator; B, C, D continue clockwise seen from
  above. Changing side A relabels geometric sides; manually tagged sides stay as tagged.
* In SIMULATED incidents a fixture detector proposes the openings that are in view of the drone camera and face
  it ("possible doorway", provenance simulated, pending review). It is a deterministic demo fixture, not a vision
  model, and says so. Blocked entrances are never inferred: the operator marks them.
* Nothing here claims structural stability, fire conditions or safe entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from ..incident.models import BoundingBox, Observation, Position, SceneAnchor, new_id, stale_at, utcnow

if TYPE_CHECKING:
    from ..incident.service import IncidentService

OPENING_LABEL = {"door": "possible doorway", "window": "possible window", "garage_door": "possible garage door",
                 "stairs": "possible stairs", "roof_hatch": "possible roof hatch", "opening": "possible opening"}
FIXTURE_DETECTOR = "sim-openings-v1 (simulation fixture, not a vision model)"


def scene_buildings(svc: "IncidentService"):
    sim = svc.rt.sim
    scene = getattr(sim.scenario, "scene", None) if sim is not None else None
    return list(scene.buildings) if scene is not None else []


def building_anchors(svc: "IncidentService", incident_id: str) -> dict[str, SceneAnchor]:
    return {a.attributes.get("building_id"): a for a in svc.store.list_anchors(incident_id)
            if a.type == "landmark" and a.attributes.get("kind") == "building"}


def register_buildings(svc: "IncidentService", incident_id: str) -> list[SceneAnchor]:
    """Register building footprints from map data (once per incident)."""
    inc = svc.store.get_incident(incident_id)
    have = building_anchors(svc, incident_id)
    out = []
    for b in scene_buildings(svc):
        if b.id in have:
            continue
        a = SceneAnchor(incident_id=incident_id, name=b.name, type="landmark", coordinate_frame=inc.enu_frame,
                        position=Position(frame=inc.enu_frame, x=b.center[0], y=b.center[1]), uncertainty_m=0.5,
                        manually_verified=False,
                        attributes={"kind": "building", "building_id": b.id, "size_ew_m": b.size_ew_m,
                                    "size_ns_m": b.size_ns_m, "height_m": b.height_m, "side_a": None,
                                    "source": "map data (simulator scene spec)"})
        out.append(svc.store.add_anchor(a))
    return out


def set_side_a(svc: "IncidentService", incident_id: str, building_id: str, compass: str, by: str = "operator") -> SceneAnchor:
    if compass not in ("N", "E", "S", "W"):
        raise ValueError("side A must be one of N, E, S, W (the compass face of the address side)")
    anchors = building_anchors(svc, incident_id)
    if building_id not in anchors:
        raise ValueError(f"unknown building {building_id}")
    a = anchors[building_id]
    a.attributes["side_a"] = compass
    a.manually_verified = True
    svc.store._put("anchors", {"id": a.id, "incident_id": a.incident_id}, a)  # noqa: SLF001 - update in place
    svc.store.append_event(incident_id, "side_a_designated", {"building_id": building_id, "compass": compass, "by": by})
    return a


def _side(anchor: SceneAnchor, x: float, y: float) -> str | None:
    from ..sim.worlds.spec import BuildingSpec

    if not anchor.attributes.get("side_a"):
        return None
    b = BuildingSpec(id="b", name="b", center=(anchor.position.x, anchor.position.y),
                     size_ew_m=anchor.attributes["size_ew_m"], size_ns_m=anchor.attributes["size_ns_m"],
                     side_a=anchor.attributes["side_a"])
    return b.side_from_point(x, y)


def structure_view(svc: "IncidentService", incident_id: str) -> dict[str, Any]:
    """Buildings, side A designation and annotations grouped by side (computed, not stored)."""
    anchors = building_anchors(svc, incident_id)
    views = [v for v in svc.store.list_observations(incident_id) if v.observation.category in ("opening", "hazard")]
    out = []
    for bid, a in anchors.items():
        sides: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": [], "D": [], "?": []}
        for v in views:
            o = v.observation
            if o.attributes.get("building") not in (None, bid):
                continue
            side = o.attributes.get("side") if o.provenance == "manual" else None
            if side is None and o.world_position is not None:
                side = _side(a, o.world_position.x + o.attributes.get("normal", [0, 0])[0],
                             o.world_position.y + o.attributes.get("normal", [0, 0])[1])
            status = "manual" if o.provenance == "manual" else v.review_status
            text = f"{o.label}, Side {side}, {'marked by the operator' if status == 'manual' else status.replace('pending', 'pending review')}" \
                if side else f"{o.label}, side unknown until side A is set"
            sides[side or "?"].append({"observation_id": o.id, "label": o.label, "status": status,
                                       "blocked": bool(o.attributes.get("blocked")), "text": text,
                                       "provenance": o.provenance})
        out.append({"building_id": bid, "name": a.name, "side_a": a.attributes.get("side_a"),
                    "source": a.attributes.get("source"), "sides": sides})
    return {"buildings": out, "note": "Openings from video are unverified until reviewed. Nothing here declares an "
                                      "entry safe or a structure stable."}


def simulate_openings(svc: "IncidentService", incident_id: str) -> list[str]:
    """SIMULATED incidents only: propose openings in view of the drone camera (deterministic demo fixture)."""
    rt = svc.rt
    if rt.sim is None or svc.wire_provenance() != "simulated":
        return []
    inc = svc.store.get_incident(incident_id)
    proposed = svc.proposed_openings.setdefault(incident_id, set())
    if not proposed:  # after a restart: never re-propose an opening already in the store
        proposed.update(v.observation.attributes.get("opening_id") for v in svc.store.list_observations(incident_id)
                        if v.observation.attributes.get("flagged_by") == "structure_fixture")
    cam = rt.sim.drone_camera(0.0)
    W, H = rt.sim.drone_intr.width, rt.sim.drone_intr.height
    cam_xy = np.array(cam.pose.position[:2])
    created = []
    for b in scene_buildings(svc):
        for op in b.openings:
            if op.id in proposed:
                continue
            (_, _), (nx, ny), _ = b.face(op.side)
            x, y = b.opening_xy(op)
            if np.dot([nx, ny], cam_xy - np.array([x, y])) <= 0:
                continue  # facing away from the camera
            uv, z = cam.project(np.array([[x, y, op.sill_m + op.height_m / 2]]))
            if z[0] <= 0 or not (0 <= uv[0][0] < W and 0 <= uv[0][1] < H):
                continue
            proposed.add(op.id)
            # image box of the opening quad (for the evidence frame), from the same camera
            rx, ry = -ny, nx
            hw = op.width_m / 2
            quad = np.array([[x + sx * rx * hw, y + sx * ry * hw, z] for sx in (-1, 1)
                             for z in (op.sill_m, op.sill_m + op.height_m)])
            q_uv, q_z = cam.project(quad)
            box = None
            if (q_z > 0).all():
                box = (float(np.clip(q_uv[:, 0].min(), 0, W - 1)), float(np.clip(q_uv[:, 1].min(), 0, H - 1)),
                       float(np.clip(q_uv[:, 0].max(), 0, W - 1)), float(np.clip(q_uv[:, 1].max(), 0, H - 1)))
            oid = new_id("OBS")
            ev = svc.save_evidence(oid, box)
            now = utcnow()
            o = Observation(id=oid, incident_id=incident_id, sensor_source_id="drone", observed_at=now, category="opening",
                            label=OPENING_LABEL[op.kind], confidence=None, detector=FIXTURE_DETECTOR,
                            world_position=Position(frame=inc.enu_frame, x=x, y=y, z=op.sill_m),
                            horizontal_uncertainty_m=1.0, position_estimation_method="simulation_truth",
                            bounding_box=BoundingBox(frame="image:drone", x0=box[0], y0=box[1], x1=box[2], y1=box[3])
                            if box else None, evidence_frame_reference=ev,
                            stale_after=stale_at(now, 600.0), provenance="simulated",
                            attributes={"building": b.id, "opening_kind": op.kind, "normal": [nx, ny],
                                        "flagged_by": "structure_fixture", "opening_id": op.id})
            svc.store.add_observation(o)
            created.append(o.id)
    return created
