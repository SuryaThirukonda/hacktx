#!/usr/bin/env python
"""Decision-engine benchmark (Overwatch §7.5, Rescue Copilot §12.5): identical payloads to every engine.

    python scripts/benchmark_decisions.py --engines openai,typesafe,strands,mock,rules --repeats 20
    python scripts/benchmark_decisions.py --engines openai --label-sensitivity

Cases are *designed* situations whose correct bounded answer follows from the task definition (they are not
field annotations). Reported per engine/task: accuracy vs the designed answer, p50/p95 latency (true latency,
generous 6 s timeout, not the runtime deadline), refusal and error rates, estimated cost from usage, and the
accuracy change when option labels are renamed to neutral IDs. Engines without keys are NOT RUN.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT, load_config  # noqa: E402
from overwatch.decisions.engines import EngineError  # noqa: E402
from overwatch.decisions.router import DecisionRouter  # noqa: E402
from overwatch.decisions.tasks import PRIMARY_QUESTION, d1_reacquire, d2_identity, d3_describe, d4_motion, d5_priority  # noqa: E402
from overwatch.faults import FaultInjector  # noqa: E402

PRICE_PER_MTOK = {"openai": 0.10, "typesafe": 0.042}


def cases() -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    # D1 reacquire
    out += [
        ("D1 occluded under canopy", d1_reacquire({"time_since_loss_s": 3.0, "occluder_near_last_seen": True,
                                                   "prediction_in_footprint": True, "subject_last_speed_mps": 1.2}), "orbit_last_seen"),
        ("D1 left the view to the left", d1_reacquire({"time_since_loss_s": 2.5, "occluder_near_last_seen": False,
                                                       "prediction_in_footprint": False, "prediction_bearing_rel_drone_deg": -70}), "yaw_left_scan"),
        ("D1 left the view to the right", d1_reacquire({"time_since_loss_s": 2.5, "occluder_near_last_seen": False,
                                                        "prediction_in_footprint": False, "prediction_bearing_rel_drone_deg": 65}), "yaw_right_scan"),
        ("D1 brief loss in view", d1_reacquire({"time_since_loss_s": 2.2, "occluder_near_last_seen": False,
                                                "prediction_in_footprint": True, "subject_last_speed_mps": 2.8}), "continue_predicted"),
        ("D1 long loss", d1_reacquire({"time_since_loss_s": 26.0, "occluder_near_last_seen": False,
                                       "prediction_in_footprint": True, "battery_pct": 40}), "hold_and_alert"),
    ]
    # D2 identity
    out += [
        ("D2 clear match", d2_identity({"reference_torso_color": "red", "candidates": [
            {"label": "A", "appearance_similarity": 0.86, "distance_from_prediction_m": 1.2, "torso_color": "red"},
            {"label": "B", "appearance_similarity": 0.21, "distance_from_prediction_m": 3.5, "torso_color": "blue"}]}, ["A", "B"]), "A"),
        ("D2 ambiguous twins", d2_identity({"reference_torso_color": "red", "candidates": [
            {"label": "A", "appearance_similarity": 0.74, "distance_from_prediction_m": 1.9, "torso_color": "red"},
            {"label": "B", "appearance_similarity": 0.71, "distance_from_prediction_m": 2.1, "torso_color": "red"}]}, ["A", "B"]), "none"),
    ]
    # D3 describe
    out += [
        ("D3 red shirt", d3_describe({"description": "red shirt, jeans", "candidates": [
            {"label": "A", "torso_color": "blue", "lower_color": "black"},
            {"label": "B", "torso_color": "red", "lower_color": "blue"},
            {"label": "C", "torso_color": "purple", "lower_color": "black"}]}, ["A", "B", "C"]), "B"),
        ("D3 no match", d3_describe({"description": "green jacket", "candidates": [
            {"label": "A", "torso_color": "blue", "lower_color": "black"},
            {"label": "B", "torso_color": "red", "lower_color": "blue"}]}, ["A", "B"]), "none"),
    ]
    # D4 motion
    out += [
        ("D4 stationary", d4_motion({"speed_mps": 0.1, "closing_mps": 0.0, "range_m": 9}), "stationary"),
        ("D4 approaching", d4_motion({"speed_mps": 3.0, "closing_mps": 2.9, "range_m": 12}), "approaching_responder"),
        ("D4 moving away", d4_motion({"speed_mps": 1.4, "closing_mps": -1.3, "range_m": 12}), "moving_away"),
    ]
    # D5 notification priority
    out += [
        ("D5 reacquired beats callout", d5_priority({"pending": [
            {"event_id": "e-1", "kind": "POSITION_CALLOUT", "text": "Subject 2 o'clock, 12 meters", "age_ms": 100},
            {"event_id": "e-2", "kind": "REACQUIRED", "text": "Subject reacquired", "age_ms": 80}]}), "e-2"),
        ("D5 repeated callout -> silence", d5_priority({"pending": [
            {"event_id": "e-3", "kind": "POSITION_CALLOUT", "text": "Subject 2 o'clock, 12 meters", "age_ms": 100}],
            "recent_alerts": [{"kind": "POSITION_CALLOUT", "age_s": 2.0}]}), "no_alert"),
        ("D5 zone entry", d5_priority({"pending": [
            {"event_id": "e-4", "kind": "ZONE_ENTRY", "text": "Subject entered Staging area", "age_ms": 50}]}), "e-4"),
    ]
    return out


def rename_options(job):
    """Neutral option IDs (opt_1..) with the same descriptions; returns (job, map neutral->original)."""
    j = copy.deepcopy(job)
    mapping: dict[str, str] = {}
    for q in j.questions:
        if q.kind != "choice":
            continue
        for i, o in enumerate(q.options):
            neutral = f"opt_{i + 1}"
            mapping[neutral] = o.value
            o.description = f"{o.description} (was: {o.value.replace('_', ' ')})" if q.name == "notification" else o.description
            o.value = neutral
    return j, mapping


async def run_engine(router: DecisionRouter, name: str, items, repeats: int, sensitivity: bool) -> dict:
    eng = router.engines[name]
    ok, why = eng.available()
    if name == "strands":
        await eng.probe()  # type: ignore[attr-defined]
        ok, why = eng.available()
    if not ok:
        return {"engine": name, "status": "NOT RUN", "reason": why}
    per_task: dict[str, dict] = {}
    lat_all: list[float] = []
    tokens = 0
    for variant in (["original", "renamed"] if sensitivity else ["original"]):
        for label, job, expected in items:
            j, mapping = (job, {}) if variant == "original" else rename_options(job)
            qn = PRIMARY_QUESTION[j.task]
            for _ in range(repeats):
                t = per_task.setdefault(f"{j.task}|{variant}", {"n": 0, "correct": 0, "refusal": 0, "error": 0, "lat": []})
                t0 = time.perf_counter()
                try:
                    res = await asyncio.wait_for(eng.decide(j, 6000.0), timeout=6.0)
                except (asyncio.TimeoutError, EngineError) as e:
                    t["n"] += 1
                    t["error"] += 1
                    continue
                ms = (time.perf_counter() - t0) * 1000.0 if not res.mock else res.latency_ms
                t["n"] += 1
                t["lat"].append(ms)
                lat_all.append(ms)
                if res.usage and res.usage.get("input_tokens"):
                    tokens += int(res.usage["input_tokens"])
                if res.status == "refusal":
                    t["refusal"] += 1
                    continue
                a = res.answer(qn)
                got = a.choice if a else None
                got = mapping.get(got, got)
                t["correct"] += int(got == expected)
    tasks = {}
    for key, v in per_task.items():
        arr = np.array(v["lat"]) if v["lat"] else None
        tasks[key] = {"n": v["n"], "accuracy": round(v["correct"] / v["n"], 3) if v["n"] else None,
                      "refusal_rate": round(v["refusal"] / v["n"], 3) if v["n"] else None,
                      "error_rate": round(v["error"] / v["n"], 3) if v["n"] else None,
                      "p50_ms": round(float(np.percentile(arr, 50)), 1) if arr is not None else None,
                      "p95_ms": round(float(np.percentile(arr, 95)), 1) if arr is not None else None}
    cost = None
    if name in PRICE_PER_MTOK and tokens:
        cost = {"input_tokens": tokens, "usd": round(tokens / 1e6 * PRICE_PER_MTOK[name], 6),
                "usd_per_1000_decisions": round(tokens / 1e6 * PRICE_PER_MTOK[name] / max(1, len(lat_all)) * 1000, 4)}
    arr = np.array(lat_all) if lat_all else None
    return {"engine": name, "status": "OK", "mock": eng.mock, "model": eng.model, "tasks": tasks,
            "p50_ms": round(float(np.percentile(arr, 50)), 1) if arr is not None else None,
            "p95_ms": round(float(np.percentile(arr, 95)), 1) if arr is not None else None, "cost": cost}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="openai,typesafe,strands,mock,rules")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--label-sensitivity", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    router = DecisionRouter(cfg, FaultInjector(), "SIMULATED")
    items = cases()
    results = []
    for name in a.engines.split(","):
        r = await run_engine(router, name, items, a.repeats, a.label_sensitivity)
        results.append(r)
        if r["status"] != "OK":
            print(f"{name:9s} NOT RUN: {r['reason']}")
            continue
        acc = {k: v["accuracy"] for k, v in r["tasks"].items()}
        print(f"{name:9s}{' (MOCK)' if r['mock'] else ''} p50 {r['p50_ms']} ms p95 {r['p95_ms']} ms  accuracy {acc}")
    out = REPO_ROOT / "evaluation" / "reports" / f"decisions-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"label": "designed cases (not field annotations)", "repeats": a.repeats,
                                                  "cases": [c[0] for c in items], "results": results}, indent=2))
    print("report:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
