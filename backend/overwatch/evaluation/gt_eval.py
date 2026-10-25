"""Ground-truth evaluation of a headless run. Every number is labelled ``synthetic`` and carries its denominator;
nothing here is a claim about real-video performance (Rescue Copilot §15.3 rule 7)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from ..sim.scenarios import MetricLimit, Scenario
from .scenario_runner import RunResult


SIZE_BINS = [("lt8px", 0.0, 8.0), ("8-16px", 8.0, 16.0), ("16-32px", 16.0, 32.0), ("32-64px", 32.0, 64.0),
             ("ge64px", 64.0, float("inf"))]  # GT box height


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _stats(v: list[float]) -> dict[str, Any]:
    if not v:
        return {"n": 0, "p50": None, "p95": None, "mean": None}
    a = np.array(v, float)
    return {"n": int(a.size), "p50": round(float(np.percentile(a, 50)), 3), "p95": round(float(np.percentile(a, 95)), 3),
            "mean": round(float(a.mean()), 3)}


def evaluate(res: RunResult, sc: Scenario) -> dict[str, Any]:
    det_total = det_hit = 0
    by_size: dict[str, list[int]] = {k: [0, 0] for k, _, _ in SIZE_BINS}  # [hits, total] by GT box height
    matched_ids: dict[str, list[str]] = defaultdict(list)
    proj_err: dict[str, list[float]] = defaultdict(list)
    lock_ok = lock_wrong = lock_none = lock_steps = 0
    range_err: list[float] = []
    range_valid_steps = 0
    subject_in_frame_follow = follow_steps = 0
    prev_match: dict[str, str] = {}
    phone: dict[str, dict[str, Any]] = defaultdict(lambda: {"boxes": 0, "gt_matched": 0, "with_range": 0, "err": [],
                                                             "bearing_err": [], "clipped": 0, "xv_n": 0, "xv_ok": 0})
    phone_seen: set[int] = set()
    for st in res.steps:
        actors = st.gt["actors"]
        states = st.gt["states"]
        vis_tracks = [t for t in st.tracks if t["visible"] and t["state"] != "TENTATIVE"]
        match_now: dict[str, str] = {}
        for aid, g in actors.items():
            if not g.get("in_frame") or g.get("visibility", 0) < 0.5 or not g.get("box_xyxy"):
                continue
            det_total += 1
            gh = g["box_xyxy"][3] - g["box_xyxy"][1]
            size_bin = next(k for k, lo, hi in SIZE_BINS if lo <= gh < hi)
            by_size[size_bin][1] += 1
            best, bi = None, 0.2
            for t in vis_tracks:
                v = _iou(t["box"], g["box_xyxy"])
                if v > bi:
                    best, bi = t, v
            if best is None:
                continue
            det_hit += 1
            by_size[size_bin][0] += 1
            match_now[aid] = best["id"]
            ids = matched_ids[aid]
            if not ids or ids[-1] != best["id"]:
                ids.append(best["id"])
            if best["xy"] is not None and st.calib.get("valid"):
                gx, gy = states[aid]["xy"]
                proj_err[st.calib.get("source", "?")].append(math.hypot(best["xy"][0] - gx, best["xy"][1] - gy))
        prev_match = match_now
        # responder phone: LiDAR range inside each vision box and the box's drone-track association, per orientation
        ph = st.phone
        if ph and ph["seq"] not in phone_seen:
            phone_seen.add(ph["seq"])
            m = phone["portrait" if ph["orientation"] in ("left", "right") else "landscape"]
            actor_of_track = {tid: aid for aid, tid in match_now.items()}
            xv = {c["phone_box"]: c["drone_track"] for c in ph["cross_view"]}
            az = {c["phone_box"]: c.get("azimuth_deg") for c in ph["cross_view"] if not c.get("clipped")}
            m["clipped"] += sum(bool(c.get("clipped")) for c in ph["cross_view"])
            for i, (rng, g) in enumerate(zip(ph["ranges"] or [], ph["gt"])):
                m["boxes"] += 1
                if g.get("actor") is None:
                    continue
                m["gt_matched"] += 1
                if rng is not None and g.get("range_m") is not None:
                    m["with_range"] += 1
                    m["err"].append(abs(rng - g["range_m"]))
                if az.get(i) is not None and g.get("bearing_deg") is not None:
                    m["bearing_err"].append(abs((az[i] - g["bearing_deg"] + 540.0) % 360.0 - 180.0))
                if xv.get(i) and xv[i] in actor_of_track:
                    m["xv_n"] += 1
                    m["xv_ok"] += int(actor_of_track[xv[i]] == g["actor"])
        # subject lock correctness (only when the true subject is visible)
        if "subject" in actors and actors["subject"].get("in_frame") and actors["subject"].get("visibility", 0) >= 0.5:
            lk = st.locks.get("subject", {})
            if lk.get("state") not in (None, "UNLOCKED"):
                lock_steps += 1
                tid = lk.get("track_id")
                if lk.get("state") == "LOCKED" and tid == match_now.get("subject"):
                    lock_ok += 1
                elif lk.get("state") == "LOCKED" and tid in match_now.values():
                    lock_wrong += 1
                else:
                    lock_none += 1
        # kinematics vs truth
        k = st.kin
        if k.get("valid") and "subject" in states and "responder" in states:
            range_valid_steps += 1
            sx, sy = states["subject"]["xy"]
            rx, ry = states["responder"]["xy"]
            range_err.append(abs(math.hypot(sx - rx, sy - ry) - (k.get("range_m") or 0.0)))
        if st.autonomy.get("mode") == "FOLLOW" and "subject" in actors:
            follow_steps += 1
            if actors["subject"].get("in_frame") and actors["subject"].get("visibility", 0) > 0.05:
                subject_in_frame_follow += 1

    id_switches = {a: max(0, len(ids) - 1) for a, ids in matched_ids.items()}
    # expected events
    exp_results = []
    for ex in sc.expected:
        hits = [e for e in res.events if e["kind"] == ex.kind and ex.t_min * 1000 <= e["ts_ms"] <= ex.t_max * 1000
                and (ex.role is None or e.get("role") == ex.role)]
        ok = bool(hits) if ex.present else not hits
        exp_results.append({"kind": ex.kind, "role": ex.role, "window_s": [ex.t_min, ex.t_max], "expect_present": ex.present,
                            "observed": len(hits), "first_ts_s": round(hits[0]["ts_ms"] / 1000, 2) if hits else None,
                            "pass": ok})
    phone_vision = {o: {"criterion": "on-device box -> nearest GT actor whose torso projects inside it",
                        "boxes": m["boxes"], "gt_matched": m["gt_matched"],
                        "range_coverage": round(m["with_range"] / m["gt_matched"], 3) if m["gt_matched"] else None,
                        "range_error_m": _stats(m["err"]), "bearing_error_deg": _stats(m["bearing_err"]),
                        "edge_clipped_boxes": m["clipped"]}
                    for o, m in phone.items()}
    cross_view = {o: {"associated": m["xv_n"], "correct": m["xv_ok"],
                      "accuracy": round(m["xv_ok"] / m["xv_n"], 3) if m["xv_n"] else None,
                      "coverage": round(m["xv_n"] / m["gt_matched"], 3) if m["gt_matched"] else None}
                  for o, m in phone.items()}
    alerts = Counter(a["kind"] for a in res.alerts)
    dec = defaultdict(lambda: {"n": 0, "acted": 0, "lat": []})
    for d in res.decisions:
        if d.get("shadow"):
            continue
        k2 = f"{d['task']}:{d['engine']}"
        dec[k2]["n"] += 1
        dec[k2]["acted"] += int(d["acted"])
        if d.get("latency_ms") is not None:
            dec[k2]["lat"].append(d["latency_ms"])
    out = {
        "label": "synthetic",
        "scenario": sc.id, "name": sc.name, "duration_s": res.duration_s, "wall_s": round(res.wall_s, 2),
        "realtime_factor": round(res.duration_s / max(res.wall_s, 1e-6), 2),
        "detection": {"criterion": "track box IoU>0.2 with GT box, visibility>=0.5", "hits": det_hit, "total": det_total,
                      "recall": round(det_hit / det_total, 3) if det_total else None,
                      "recall_by_size": {k: {"hits": h, "total": n, "recall": round(h / n, 3) if n else None}
                                         for k, (h, n) in by_size.items()}},
        "tracking": {"id_switches": id_switches, "total_id_switches": int(sum(id_switches.values()))},
        "projection_error_m": {k: _stats(v) for k, v in proj_err.items()},
        "subject_lock": {"steps_subject_visible_and_locked": lock_steps, "correct": lock_ok, "wrong_target": lock_wrong,
                         "not_locked_on_anything": lock_none,
                         "correct_fraction": round(lock_ok / lock_steps, 3) if lock_steps else None},
        "range_error_m": {**_stats(range_err), "valid_steps": range_valid_steps, "total_steps": len(res.steps)},
        "autonomy": {"follow_steps": follow_steps,
                     "subject_in_frame_fraction": round(subject_in_frame_follow / follow_steps, 3) if follow_steps else None},
        "phone_vision": phone_vision,
        "cross_view": cross_view,
        "expected_events": exp_results,
        "alerts": dict(alerts),
        "decisions": {k: {"n": v["n"], "acted": v["acted"], "sim_latency_ms": _stats(v["lat"])} for k, v in dec.items()},
        "registration": res.registration,
        "operator_actions": res.operator_log,
        "events_timeline": [(round(e["ts_ms"] / 1000, 2), e["kind"], e["text"]) for e in res.events][:200],
    }
    if sc.conflicts:
        out["decision_adjudication"] = adjudicate(res, sc)
    out["engine"] = res.engine
    out["memory"] = memory_persistence(res)
    if getattr(sc, "world_eval", None) is not None:  # world-specific ground-truth scoring (search, flood, fire)
        out["world"] = sc.world_eval(res, sc)
    out["expected_metrics"] = [_check_limit(out, lim) for lim in sc.limits]
    checks = [e["pass"] for e in exp_results] + [m["pass"] for m in out["expected_metrics"]]
    out["expected_pass"] = all(checks) if checks else None
    return out


def adjudicate(res: RunResult, sc: Scenario) -> dict[str, Any]:
    """Score D5 on scripted conflicts: the engine's choice, the fixed-priority rules baseline (logged with every
    decision) and what was actually announced, each against the answer written into the scenario (S16)."""
    kind_of = {e["event_id"]: e["kind"] for e in res.events}
    by_conflict: dict[str, list[str]] = defaultdict(list)
    for e in res.events:
        cid = (e.get("data") or {}).get("scripted_conflict")
        if cid:
            by_conflict[cid].append(e["event_id"])
    d5 = [d for d in res.decisions if d["task"] == "D5_priority" and not d.get("shadow")]
    rows = []
    for c in sc.conflicts:
        ids = set(by_conflict.get(c.id, []))
        rec = next((d for d in d5 if ids and ids & set(d.get("event_ids") or [])), None)
        said = [a for a in res.alerts if ids & set(a.get("evidence_event_ids") or [])]

        def as_kind(v: str | None) -> str | None:
            return None if v is None else ("no_alert" if v == "no_alert" else kind_of.get(v, v))

        rules = as_kind((rec or {}).get("facts", {}).get("_rules_baseline")) if rec else None
        ai_engine = rec["engine"] if rec and rec["engine"] != "rules" else None
        ai = as_kind(rec.get("selected")) if ai_engine else None
        delivered = said[0]["kind"] if said else "no_alert"
        rows.append({"id": c.id, "t_s": c.t, "candidates": [e.kind for e in c.events], "truth": c.gt, "why": c.why,
                     "decision_id": rec["decision_id"] if rec else None, "engine": rec["engine"] if rec else None,
                     "mock": bool(rec and rec.get("mock")), "ai": ai, "ai_acted": bool(rec and rec.get("acted")),
                     "rules": rules, "delivered": delivered,
                     "latency_ms": rec.get("latency_ms") if rec else None})

    def acc(key: str) -> dict[str, Any]:
        scored = [r for r in rows if r[key] is not None]
        ok = sum(r[key] == r["truth"] for r in scored)
        return {"correct": ok, "scored": len(scored), "accuracy": round(ok / len(scored), 3) if scored else None}

    engines = sorted({r["engine"] for r in rows if r["engine"]})
    return {"criterion": "scripted conflicts; ground truth written in the scenario before any run",
            "engines": engines, "mock": any(r["mock"] for r in rows),
            "logged": sum(r["decision_id"] is not None and r["rules"] is not None for r in rows), "n": len(rows),
            "ai": acc("ai"), "rules": acc("rules"), "delivered": acc("delivered"),
            "ai_rules_disagreements": sum(r["ai"] is not None and r["rules"] is not None and r["ai"] != r["rules"]
                                          for r in rows),
            "conflicts": rows}


def _lookup(report: dict[str, Any], path: str) -> Any:
    cur: Any = report
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _check_limit(report: dict[str, Any], lim: MetricLimit) -> dict[str, Any]:
    v = _lookup(report, lim.path)
    ok = isinstance(v, (int, float)) and (lim.max is None or v <= lim.max) and (lim.min is None or v >= lim.min)
    return {"metric": lim.path, "value": v, "min": lim.min, "max": lim.max, "pass": bool(ok)}


def memory_persistence(res: RunResult) -> dict[str, Any]:
    """Identity persistence per simulated person: how many tracker ids versus object-memory ids it was given over the
    run (1 is perfect), counted on steps where a track overlaps the person (IoU >= 0.3, visibility >= 0.5). Also how
    often the memory gave one object id to two different people (wrong merges)."""
    track_ids: dict[str, set] = defaultdict(set)
    obj_ids: dict[str, set] = defaultdict(set)
    obj_people: dict[str, set] = defaultdict(set)
    track_people: dict[str, set] = defaultdict(set)
    obj_track_people: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for st in res.steps:
        oid_of = {m["track_id"]: m["oid"] for m in (st.memory or []) if m.get("track_id")}
        for aid, g in (st.gt.get("actors") or {}).items():
            if not g.get("box_xyxy") or g.get("visibility", 0) < 0.5:
                continue
            best, best_iou = None, 0.3
            for t in st.tracks:
                if t["box"] and t["visible"]:
                    v = _iou(t["box"], g["box_xyxy"])
                    if v > best_iou:
                        best, best_iou = t["id"], v
            if best is None:
                continue
            track_ids[aid].add(best)
            track_people[best].add(aid)
            if best in oid_of:
                obj_ids[aid].add(oid_of[best])
                obj_people[oid_of[best]].add(aid)
                obj_track_people[oid_of[best]][best].add(aid)
    people = sorted(track_ids)
    per = {a: {"tracker_ids": len(track_ids[a]), "memory_ids": len(obj_ids[a])} for a in people}
    return {"criterion": "ids given to each simulated person while a track overlaps them (1 = perfect)",
            "tracker_id_count": int(sum(v["tracker_ids"] for v in per.values())),
            "memory_id_count": int(sum(v["memory_ids"] for v in per.values())),
            "people": len(people),
            # one tracker track that moved from one person to another (the memory follows the track it is bound to)
            "tracker_swaps": int(sum(1 for ps in track_people.values() if len(ps) > 1)),
            # the memory attached a new track of a different person to a remembered object
            "wrong_merges": int(sum(1 for oid, by_t in obj_track_people.items()
                                     if len({next(iter(ps)) for ps in by_t.values() if len(ps) == 1}) > 1)),
            "per_person": per}
