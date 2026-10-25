"""3D view of the simulator (dashboard page /3d): static world, the drone model's mesh, and a 20 Hz stream of the
true drone pose, people, camera footprint, autonomy state and the D1 decisions that steered the drone.

Simulation only: everything here is ground truth from the simulator, labelled as such, alongside what the
pipeline believed (lock estimate, last-seen position, search radius) so the two can be compared.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from scipy.spatial.transform import Rotation

X2_DIR = Path(__file__).resolve().parents[2] / "third_party" / "skydio_x2" / "assets"
ASSETS = {"x2.obj": ("X2_lowpoly.obj", "text/plain"),
          "x2.png": ("X2_lowpoly_texture_SpinningProps_1024.png", "image/png")}


def _hex(bgr) -> str:
    b, g, r = (int(v) for v in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


MAX_TREES_3D = 3000


def world_payload(sim) -> dict[str, Any]:
    w = sim.world
    trees = w.trees
    note = None
    if len(trees) > MAX_TREES_3D:  # the 1.5 km forest: the browser gets the trees nearest the drone's start
        d = sim.drone.true_xy()
        trees = sorted(trees, key=lambda t: (t.xy[0] - d[0]) ** 2 + (t.xy[1] - d[1]) ** 2)[:MAX_TREES_3D]
        note = f"3D view shows the {MAX_TREES_3D} trees nearest the drone of {len(w.trees)}"
    hz = getattr(sim.scenario, "hazards", None)
    return {
        "available": True, "label": "simulator ground truth", "note": note,
        "bounds": w.bounds, "ortho": f"/api/world/ortho.jpg?w={w.name}",
        "trees": [{"id": t.oid, "xy": t.xy, "trunk_r": t.trunk_r, "trunk_h": t.trunk_h, "canopy_r": t.canopy_r,
                   "canopy_z": t.canopy_z, "color": _hex(t.color)} for t in trees],
        "boxes": [{"id": b.oid, "kind": b.kind, "center": b.center_xy, "size": b.size_lwh, "heading": b.heading_deg,
                   "z0": b.z0, "color": _hex(b.color),
                   "openings": [{"id": op.oid, "center": op.center_xy, "normal": op.normal_xy, "width": op.width,
                                 "z0": op.z0, "z1": op.z1, "color": _hex(op.color)} for op in b.openings]}
                  for b in w.boxes],
        "markers": [{"id": m.marker_id, "xy": m.xy, "size": m.size_m} for m in w.markers],
        "zones": [{"name": z.name, "polygon": z.polygon} for z in w.zones],
        "geofence": w.geofence,
        "hazards": hz.world_3d() if hz is not None and hasattr(hz, "world_3d") else None,
        "drone_mesh": {"obj": "/api/sim3d/asset/x2.obj", "texture": "/api/sim3d/asset/x2.png",
                       "scale": 0.01, "quat_wxyz": [0.0, 0.0, 0.70710678, 0.70710678],
                       "source": "Skydio X2, MuJoCo Menagerie (Apache-2.0)"},
    }


_HAZ_CACHE: dict[str, Any] = {}


def hazards_payload(sim) -> dict[str, Any] | None:
    """Water, fire, keep-out and smoke now (screening models, labelled), recomputed at most once per simulated second."""
    hz = sim.scenario.hazards
    if hz is None:
        return None
    key = (sim.scenario.id, int(sim.t_s))
    if _HAZ_CACHE.get("key") != key:
        lay = hz.layers(sim.t_s)
        areas = [{"kind": a["kind"], "polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in a["polygon"][:: max(1, len(a["polygon"]) // 120)]]}
                 for a in lay.get("areas", []) if len(a["polygon"]) >= 3]
        _HAZ_CACHE.update(key=key, value={"label": lay.get("label"), "level_m": lay.get("level_m"), "areas": areas,
                                          "t_s": round(sim.t_s, 1)})
    return _HAZ_CACHE["value"]


def _drone_quat(d) -> list[float]:
    if hasattr(d, "quat_wxyz"):
        return d.quat_wxyz()
    # kinematic model: yaw from heading (clockwise from north), nose-down pitch negative, right-down roll positive
    psi = math.radians(90.0 - d.heading)
    x, y, z, w = Rotation.from_euler("ZYX", [psi, math.radians(-d.pitch), math.radians(d.roll)]).as_quat()
    return [round(float(w), 5), round(float(x), 5), round(float(y), 5), round(float(z), 5)]


def frame_payload(rt) -> dict[str, Any]:
    sim, p = rt.sim, rt.pipeline
    d = sim.drone
    pos = d.pos.copy()
    pos[:2] += getattr(d, "wind_offset", np.zeros(2))
    try:
        foot = sim.drone_camera(0.0).footprint(80.0)
    except Exception:  # noqa: BLE001 - camera looking at the sky
        foot = None
    gt = sim.last_gt.get("actors", {})
    actors = []
    for a in sim.actors:
        if not a.active:
            continue
        ap = a.app
        actors.append({"id": a.id, "role": a.role, "xy": [round(float(a.pos[0]), 3), round(float(a.pos[1]), 3)],
                       "heading": round(float(a.heading), 1), "height": ap.height_m, "shirt": _hex(ap.shirt),
                       "pants": _hex(ap.pants), "vest": ap.vest, "skin": _hex(ap.skin), "hair": _hex(ap.hair),
                       "sleeves": ap.sleeves, "trousers": ap.trousers, "jacket": _hex(ap.jacket) if ap.jacket else None,
                       "hat": _hex(ap.hat) if ap.hat else None, "backpack": _hex(ap.backpack) if ap.backpack else None,
                       "phase": round(float(a.phase), 2), "speed": round(float(np.linalg.norm(a.vel)), 2),
                       "visible": round(float(gt.get(a.id, {}).get("visibility", 0.0) or 0.0), 2)})
    now = rt.clock.now_ms()
    with p.lock:
        lk = p.locks["subject"]
        subj = lk.snapshot(now)
        est = [round(float(v), 2) for v in lk.kf.position] if lk.kf is not None else subj.predicted_xy_m
        resp = p.locks["responder"].snapshot(now)
        auto = p.autonomy.state()
        decisions = [r for r in list(p.decision_log)[-80:] if r.task == "D1_reacquire" and not r.shadow][-14:]
        pursuit = None
        b = p.belief
        if b is not None:  # the pipeline's belief (not ground truth): particles per hypothesis, viewpoints, route
            w = b.hyp_weights()
            cands = p.pursuit_candidates[1] if p.pursuit_candidates else []
            pick = (p.pursuit_route or {}).get("candidate_id")
            pursuit = {"particles": b.sample(per_hypothesis=120),
                       "hypotheses": [{"id": h.hid, "label": h.label, "weight": round(w[h.hid], 3)} for h in b.hyps],
                       "candidates": [{"id": c["id"], "xy": c["target_xy"], "coverage": c["coverage"],
                                       "unsafe": c["unsafe"], "chosen": c["id"] == pick} for c in cands[:5]],
                       "footprint": next((c["footprint"] for c in cands if c["id"] == pick), None),
                       "route": (p.pursuit_route or {}).get("waypoints") or []}
    dec = []
    for r in decisions:
        ans = next((a for a in r.answers if a.name == "search_action"), None)
        probs = sorted((ans.probabilities or {}).items(), key=lambda kv: -kv[1])[:4] if ans else []
        dec.append({"id": r.decision_id, "t_ms": r.request_ts_ms, "engine": r.engine, "model": r.model, "mock": r.mock,
                    "status": r.status, "selected": r.selected, "confidence": ans.confidence if ans else None,
                    "probabilities": probs, "latency_ms": r.latency_ms, "acted": r.acted, "reason": r.reason,
                    "baseline": r.facts.get("_rules_baseline"), "lost_s": r.facts.get("time_since_loss_s"),
                    "cover": r.facts.get("occluder_near_last_seen"), "predicted": r.facts.get("predicted_position"),
                    "at": rt.decision_marks.get(r.decision_id)})
    lc = auto.last_command
    hz = getattr(sim.scenario, "hazards", None)
    return {
        "type": "frame", "t_s": round(sim.t_s, 3), "scenario": sim.scenario.id, "scenario_name": sim.scenario.name,
        "duration_s": sim.scenario.duration_s, "paused": rt.paused, "speed": rt.speed,
        "engine": p.router.resolve_primary(), "engine_model": p.router.engines[p.router.resolve_primary()].model,
        "ai_paused": p.router.ai_paused, "pause_reason": p.router.pause_reason,
        "drone": {"model": getattr(d, "model_name", "kinematic Mini 2 stand-in"),
                  "pos": [round(float(v), 3) for v in pos], "quat": _drone_quat(d),
                  "heading": round(float(d.heading), 1), "pitch": round(float(d.pitch), 1), "roll": round(float(d.roll), 1),
                  "vel": [round(float(v), 2) for v in d.vel], "gimbal_pitch": round(float(d.gimbal_pitch), 1),
                  "battery": round(float(d.battery), 1),
                  "thrusts": d.rotor_thrusts() if hasattr(d, "rotor_thrusts") else None},
        "footprint": [[round(x, 2), round(y, 2)] for x, y in foot] if foot else None,
        "actors": actors,
        "subject": {"state": subj.state, "track_id": subj.track_id, "est_xy": est,
                    "last_seen_xy": subj.last_seen_xy_m, "search_radius": subj.search_radius_m,
                    "lost_ms": subj.lost_ms},
        "responder": {"state": resp.state, "track_id": resp.track_id},
        "autonomy": {"mode": auto.mode, "fsm": auto.fsm, "primitive": auto.primitive,
                     "primitive_source": auto.primitive_source,
                     "geofence_margin_m": auto.safety.geofence_margin_m if auto.safety else None,
                     "clamps": auto.safety.clamps if auto.safety else [],
                     "command": {"fwd": lc.forward_mps, "right": lc.right_mps, "up": lc.up_mps, "yaw": lc.yaw_rate_dps}
                     if lc else None},
        "hazards": hz.frame_3d(sim.t_s) if hz is not None and hasattr(hz, "frame_3d") else None,
        "decisions": dec,
        "pursuit": pursuit,
        "crew_route": _crew_route(p),
    }


def _crew_route(p) -> dict[str, Any] | None:
    st = p.crew.view() if getattr(p, "crew", None) is not None else None
    r = (st or {}).get("route")
    if not st:
        return None
    return {"kind": st["kind"], "waypoints": [[round(float(x), 1), round(float(y), 1)] for x, y in (r or {}).get("waypoints", [])],
            "label": (f"crew route {r.get('label') or r.get('id', '')} {r['length_m']:.0f} m" if r else (st.get("why") or "no safe route")),
            "ok": r is not None}


def register(app: FastAPI, rt) -> None:
    @app.get("/api/sim3d/world")
    def sim3d_world() -> dict[str, Any]:
        if rt.sim is None:
            return {"available": False, "reason": "the 3D view shows the simulator (SIMULATED mode)"}
        return world_payload(rt.sim)

    @app.get("/api/sim3d/asset/{name}")
    def sim3d_asset(name: str) -> FileResponse:
        if name not in ASSETS:
            raise HTTPException(404, "unknown asset")
        fname, media = ASSETS[name]
        path = X2_DIR / fname
        if not path.exists():
            raise HTTPException(404, "asset missing")
        return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})

    @app.websocket("/ws/sim3d")
    async def ws_sim3d(ws: WebSocket) -> None:
        await ws.accept()
        last_world = None
        try:
            while True:
                if rt.sim is None:
                    await ws.send_json({"type": "unavailable"})
                    await asyncio.sleep(1.0)
                    continue
                key = (rt.sim.scenario.id, id(rt.sim.world))
                if key != last_world:  # scenario (and its scenario-only objects) changed
                    last_world = key
                    await ws.send_json({"type": "world", **world_payload(rt.sim)})
                await ws.send_json(frame_payload(rt))
                await asyncio.sleep(0.05)
        except (WebSocketDisconnect, RuntimeError):
            return
