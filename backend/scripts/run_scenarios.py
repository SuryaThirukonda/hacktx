#!/usr/bin/env python
"""Run simulator scenarios headless through the production pipeline and write a synthetic evaluation report.

    python scripts/run_scenarios.py                 # all scenarios
    python scripts/run_scenarios.py demo OW1 S05    # selected
    python scripts/run_scenarios.py --quick demo    # print only the summary
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from overwatch.config import REPO_ROOT, load_config  # noqa: E402
from overwatch.evaluation.gt_eval import evaluate  # noqa: E402
from overwatch.evaluation.scenario_runner import HeadlessRunner  # noqa: E402
from overwatch.sim.scenarios import all_scenarios  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(REPO_ROOT / "evaluation" / "reports"))
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--engine", choices=["openai", "typesafe", "strands"], default=None,
                    help="call this decision engine for real (needs its key / server); default: mock + rules")
    args = ap.parse_args()
    scen = all_scenarios()
    ids = args.scenarios or list(scen)
    reports = []
    for sid in ids:
        runner = HeadlessRunner(load_config(), sid, args.seed, args.engine)
        res = runner.run(args.duration)
        rep = evaluate(res, scen[sid])
        reports.append(rep)
        exp = rep["expected_pass"]
        print(f"[{sid:5s}] {rep['name'][:38]:38s} expected={'PASS' if exp else ('n/a' if exp is None else 'FAIL')} "
              f"recall={rep['detection']['recall']} idsw={rep['tracking']['total_id_switches']} "
              f"lock_ok={rep['subject_lock']['correct_fraction']} range_err_p50={rep['range_error_m']['p50']} "
              f"proj={ {k: v['p50'] for k, v in rep['projection_error_m'].items()} } "
              f"rt_x{rep['realtime_factor']}", flush=True)
        if args.quick or exp is False:
            for e in rep["expected_events"] + rep["expected_metrics"]:
                if args.quick or not e["pass"]:
                    print("    ", "PASS" if e["pass"] else "FAIL", e)
    cross = pursuit_cross_check(reports)
    if cross is not None:
        print(f"[PP5-7] routes differ: {'PASS' if cross['pass'] else 'FAIL'} {cross}", flush=True)
    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"label": "synthetic", "seed": args.seed, "reports": reports,
                                                 "pursuit_cross_check": cross}, indent=2, default=str))
    lines = ["# Scenario evaluation (synthetic)", "", "All values come from the simulator's ground truth. They validate the",
             "pipeline's logic and geometry, **not** detector accuracy on real video.", "",
             "| Scenario | Expected events + limits | Detection recall | ID switches | Subject lock correct | Range err p50 (m) | Projection err p50 (m) |",
             "|---|---|---|---|---|---|---|"]
    for r in reports:
        exp = r["expected_pass"]
        proj = ", ".join(f"{k}: {v['p50']}" for k, v in r["projection_error_m"].items())
        lines.append(f"| {r['scenario']} {r['name']} | {'PASS' if exp else ('n/a' if exp is None else 'FAIL')} | "
                     f"{r['detection']['recall']} ({r['detection']['hits']}/{r['detection']['total']}) | "
                     f"{r['tracking']['total_id_switches']} | {r['subject_lock']['correct_fraction']} | "
                     f"{r['range_error_m']['p50']} | {proj} |")
    checked = [r for r in reports if r["expected_metrics"]]
    if checked:
        lines += ["", "## Metric limits", "", "| Scenario | Metric | Value | Limit | Result |", "|---|---|---|---|---|"]
        for r in checked:
            for m in r["expected_metrics"]:
                lim = " and ".join(x for x in (f">= {m['min']}" if m["min"] is not None else "",
                                               f"<= {m['max']}" if m["max"] is not None else "") if x)
                lines.append(f"| {r['scenario']} | `{m['metric']}` | {m['value']} | {lim} | {'PASS' if m['pass'] else 'FAIL'} |")
    if cross is not None:
        lines += ["", "## Pursuit routing: PP5, PP6 and PP7 fly different routes", "",
                  f"{'PASS' if cross['pass'] else 'FAIL'}: {cross['why']}", "",
                  "| Scenario | D12 picks (time s: viewpoint) |", "|---|---|"]
        for sid, picks in cross["picks"].items():
            lines.append(f"| {sid} | {', '.join(f'{t}: {p}' for t, p in picks)} |")
    phone_rows = [(r["scenario"], o, v, r["cross_view"].get(o, {})) for r in reports for o, v in r["phone_vision"].items()
                  if v["gt_matched"]]
    if phone_rows:
        lines += ["", "## Responder phone: LiDAR range in vision boxes, cross-view association", "",
                  "| Scenario | Orientation | Boxes with GT person | Range coverage | Range err p50 (m) | Bearing err p50 (deg) | Drone-track association accuracy |",
                  "|---|---|---|---|---|---|---|"]
        for sid, o, v, xv in phone_rows:
            acc = (f"{xv.get('accuracy')} ({xv.get('correct')}/{xv.get('associated')}), coverage {xv.get('coverage')}"
                   if xv.get("associated") else "n/a")
            lines.append(f"| {sid} | {o} | {v['gt_matched']} | {v['range_coverage']} | {v['range_error_m']['p50']} | "
                         f"{v['bearing_error_deg']['p50']} | {acc} |")
    for r in reports:
        adj = r.get("decision_adjudication")
        if not adj:
            continue
        eng = ", ".join(adj["engines"]) or "none"
        lines += ["", f"## {r['scenario']}: decision engine vs fixed rules (engines: {eng}"
                      f"{' - MOCK, not a model' if adj['mock'] else ''})", "",
                  f"AI {adj['ai']['correct']}/{adj['ai']['scored']} · rules {adj['rules']['correct']}/{adj['rules']['scored']} · "
                  f"announced {adj['delivered']['correct']}/{adj['delivered']['scored']} correct · "
                  f"{adj['ai_rules_disagreements']} AI/rules disagreements", "",
                  "| Conflict | Candidates | Ground truth | AI | Rules | Announced | Why |", "|---|---|---|---|---|---|---|"]
        for c in adj["conflicts"]:
            lines.append(f"| {c['id']} | {', '.join(c['candidates'])} | {c['truth']} | {c['ai'] or '-'} | {c['rules'] or '-'} | "
                         f"{c['delivered']} | {c['why']} |")
        if r.get("engine", {}).get("note"):
            lines.append(f"\n{r['engine']['note']}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    latest = Path(args.out) / "latest.json"
    latest.write_text((out / "report.json").read_text())
    print(f"report: {out}")
    ok = all(r["expected_pass"] in (True, None) for r in reports) and (cross is None or cross["pass"])
    return 0 if ok else 1


def pursuit_cross_check(reports: list[dict]) -> dict | None:
    """PP5, PP6 and PP7 share the chase; they must not share the route. PP5 (one hypothesis) flies the street
    ahead; PP6 (two clouds, no description) and PP7 (the description) must fly different viewpoints after the
    operator speaks in PP7, because the description reweights the hypotheses."""
    by = {r["scenario"]: r for r in reports if r["scenario"] in ("PP5", "PP6", "PP7")}
    if len(by) < 3:
        return None
    picks = {sid: [(p["t_s"], p["pick"]) for p in (r.get("world") or {}).get("belief", {}).get("picks", [])]
             for sid, r in by.items()}
    lf = (by["PP7"].get("world") or {}).get("belief", {}).get("look_for_at_s")
    after = lambda sid: [p for t, p in picks[sid] if lf is not None and t > lf]  # noqa: E731
    pp6, pp7 = after("PP6"), after("PP7")
    differ_67 = bool(pp6 and pp7 and set(pp6) != set(pp7))
    first5 = (by["PP5"].get("world") or {}).get("belief", {}).get("first_pick_label") or ""
    ok5 = str(first5).startswith("street ahead")
    return {"pass": differ_67 and ok5, "picks": picks, "pp6_after_look_for": pp6, "pp7_after_look_for": pp7,
            "why": f"PP5 first flies '{first5}'; after the description at {lf} s PP6 flies {sorted(set(pp6))} and "
                   f"PP7 flies {sorted(set(pp7))}"}


if __name__ == "__main__":
    raise SystemExit(main())
