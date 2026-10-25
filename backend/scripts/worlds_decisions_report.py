#!/usr/bin/env python
"""Seed-paired engine-versus-rules report for decision tasks D6 to D11, on cases generated from the world models.

Each seed builds one case per task from the simulated worlds (pursuit obstacles, search sectors and leads, flood
routes and dispatch, fire openings). The same job goes through the full decision router (unsafe options removed,
answer and probability validation, confidence gate) with the chosen engine; the rules baseline is computed on the
same job. Reported per task: agreement with rules, acted fraction, rejected answers, latency.

    python scripts/worlds_decisions_report.py                 # mock engine (no key needed), 20 seeds
    python scripts/worlds_decisions_report.py --engine openai --seeds 8

Results are simulated cases: they compare an engine with the rules, not with field outcomes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT, load_config  # noqa: E402
from overwatch.decisions.router import DecisionRouter  # noqa: E402
from overwatch.decisions.tasks import BUILDERS  # noqa: E402
from overwatch.faults import FaultInjector  # noqa: E402
from overwatch.sim.hazards.water import candidate_routes  # noqa: E402
from overwatch.sim.worlds import fire, flood, pursuit, sar  # noqa: E402


def d6_case(rng: np.random.Generator) -> dict:
    """An obstacle ahead of the drone along the pursuit street, clearances from the world geometry."""
    w = pursuit.build_world()
    obstacles = [(b.center_xy, b.size_lwh[2]) for b in w.boxes] + [(t.xy, t.canopy_z + 1.0) for t in w.trees]
    x, y, alt = rng.uniform(-50, 80), rng.uniform(-10, 10), rng.uniform(4.0, 9.0)
    ahead = sorted((math.hypot(ox - x, oy - y), h) for (ox, oy), h in obstacles if ox > x and h > alt - 1)
    rng_m = ahead[0][0] if ahead else 30.0
    return {"obstacle_range_m": round(min(rng_m, 30.0) * rng.uniform(0.3, 1.0), 1),
            "closing_mps": round(rng.uniform(-0.5, 3.0), 1), "clearance_left_m": round(rng.uniform(0.5, 8.0), 1),
            "clearance_right_m": round(rng.uniform(0.5, 8.0), 1), "altitude_m": round(alt, 1), "ceiling_m": 60.0,
            "world": "pursuit (simulated)"}


def d7_case(rng: np.random.Generator) -> dict:
    leads = []
    for i in range(int(rng.integers(2, 5))):
        x, y = rng.uniform(-110, 110, 2)
        leads.append({"id": f"L{i + 1}", "kind": str(rng.choice(["camera candidate", "radio report", "ground team"])),
                      "age_s": round(float(rng.uniform(5, 900))), "distance_m": round(float(math.hypot(x - sar.IPP[0], y - sar.IPP[1]))),
                      "uncertainty_m": None if rng.random() < 0.3 else round(float(rng.uniform(1, 15)), 1)})
    return {"leads": leads, "world": "search and rescue (simulated)"}


def d8_case(rng: np.random.Generator) -> dict:
    sweep = [(float(rng.uniform(-110, 0)), float(rng.uniform(-110, 0)))]
    for _ in range(4):
        sweep.append((sweep[-1][0] + float(rng.uniform(-30, 60)), sweep[-1][1] + float(rng.uniform(-30, 60))))
    poa = sar.sector_poa_after(sweep, 30.0)
    return {"sectors": [{"id": k, "poa": poa["after_sweep"][k], "coverage_pct": poa["coverage_pct"][k],
                         "distance_m": round(float(math.hypot(np.mean([p[0] for p in v]) - sweep[-1][0],
                                                              np.mean([p[1] for p in v]) - sweep[-1][1])))}
                        for k, v in sar.SECTORS.items()], "world": "search and rescue (simulated)"}


def d9_case(rng: np.random.Generator) -> dict:
    t = float(rng.uniform(10, 45))
    goal = str(rng.choice(["-50,-30", "+0,-30", "+50,-30", "-50,+10"]))
    opts = candidate_routes(flood.roads(), flood.flood_model(), t, "staging", goal, k=3)
    return {"routes": opts, "water_level_m": round(flood.flood_model().level(t), 2), "world": "flood (simulated)"}


def d10_case(rng: np.random.Generator) -> dict:
    reqs = [{"id": f"Q{i + 1}", "priority": int(rng.integers(1, 4))} for i in range(int(rng.integers(1, 4)))]
    teams = [{"id": f"crew-{j + 1}", "available": bool(rng.random() > 0.25),
              "eta_s": {q["id"]: round(float(rng.uniform(120, 900))) for q in reqs}} for j in range(2)]
    return {"requests": reqs, "teams": teams, "world": "flood (simulated)"}


def d11_case(rng: np.random.Generator) -> dict:
    f = fire.fire_model()
    t = float(rng.uniform(5, 50))
    op = fire.BUILDING.openings[int(rng.integers(0, len(fire.BUILDING.openings)))]
    x, y = fire.BUILDING.opening_xy(op)
    (_, _), (nx, ny), _ = fire.BUILDING.face(op.side)
    s = float(f.smoke(t)[f.grid.cell(x + nx * 2, y + ny * 2)])
    return {"opening": op.id, "side": op.side, "in_view": bool(rng.random() > 0.15), "smoke_layer": s > 0.25,
            "smoke_density": round(s, 2), "blocked_marked": bool(rng.random() < 0.15),
            "obstructed_fraction": round(float(rng.uniform(0, 0.7)), 2), "world": "fire (simulated)"}


CASES = {"D6_obstacle": d6_case, "D7_lead_triage": d7_case, "D8_next_sector": d8_case, "D9_route": d9_case,
         "D10_dispatch": d10_case, "D11_opening": d11_case}


async def run(engine: str, seeds: int) -> dict:
    cfg = load_config()
    cfg.decisions.shadow = []
    cfg.decisions.deadline_ms = 6000.0  # a report measures the engine, not the runtime deadline
    router = DecisionRouter(cfg, FaultInjector(), "SIMULATED")
    router.set_engines(primary=engine)
    used = router.resolve_primary()
    out = {}
    for task, gen in CASES.items():
        rows = []
        for seed in range(seeds):
            job = BUILDERS[task](gen(np.random.default_rng(1000 + seed)))
            rec = (await router.run(job, lambda: 0.0))[0]
            base = rec.facts.get("_rules_baseline")
            rows.append({"seed": seed, "engine": rec.engine, "selected": rec.selected, "rules": base, "acted": rec.acted,
                         "status": rec.status, "reason": rec.reason, "latency_ms": rec.latency_ms,
                         "removed_options": rec.facts.get("removed_options", [])})
        n = len(rows)
        model_rows = [r for r in rows if r["engine"] != "rules"]
        out[task] = {
            "n": n,
            "agreement_with_rules": round(sum(r["selected"] == r["rules"] for r in model_rows) / len(model_rows), 3)
            if model_rows else None,
            "acted_fraction": round(sum(r["acted"] for r in rows) / n, 3),
            "fell_back_to_rules": sum(r["engine"] == "rules" for r in rows),
            "rejected_invalid": sum("invalid" in (r["reason"] or "") for r in rows),
            "cases_with_unsafe_options_removed": sum(bool(r["removed_options"]) for r in rows),
            "latency_ms_p50": float(np.percentile([r["latency_ms"] for r in rows], 50)),
            "rows": rows,
        }
    return {"engine_requested": engine, "engine_used": used, "mode_label": router.mode_label(),
            "label": "simulated cases from the world models; agreement is with the rules, not with field outcomes",
            "tasks": out, "budget": {"model_calls": router.session_model_calls, "tokens": router.session_tokens}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="mock")
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args()
    rep = asyncio.run(run(a.engine, a.seeds))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = REPO_ROOT / "evaluation" / "benchmarks" / f"{stamp}-worlds-decisions-{a.engine}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    lines = [f"# D6 to D11: {rep['engine_used']} versus rules ({stamp})", "", f"**{rep['label']}.**",
             f"Engine requested {rep['engine_requested']}, used {rep['engine_used']} ({rep['mode_label']}).", "",
             "| Task | Seeds | Agreement with rules | Acted | Fell back to rules | Rejected answers | Unsafe options removed | p50 ms |",
             "|---|---|---|---|---|---|---|---|"]
    for t, v in rep["tasks"].items():
        lines.append(f"| {t} | {v['n']} | {v['agreement_with_rules']} | {v['acted_fraction']} | {v['fell_back_to_rules']} | "
                     f"{v['rejected_invalid']} | {v['cases_with_unsafe_options_removed']} | {v['latency_ms_p50']:.0f} |")
    lines += ["", f"Model calls {rep['budget']['model_calls']}, tokens {rep['budget']['tokens']}."]
    (d / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"report: {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
