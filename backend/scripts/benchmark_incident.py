"""SIMULATED incident benchmark: the demo incident run end to end with a scripted operator and responder.

What it measures: the system's own latencies and counts on the guaranteed demo world (DEMO-1), computed from the
incident event log, plus localization error against simulator ground truth. What it does not measure: human
performance or detector accuracy on real video. The operator is a script that reviews each candidate after a fixed
delay using simulator ground truth (the volunteer is the missing person; anything else is rejected), approves the
responder notification the rules propose, and the responder script acknowledges after a fixed delay.

Writes evaluation/benchmarks/<timestamp>/incident_benchmark.json and summary.md. Usage:
    .venv/bin/python scripts/benchmark_incident.py [--seconds 60]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from overwatch.api.app import create_app  # noqa: E402
from overwatch.incident.metrics import compute_metrics  # noqa: E402
from overwatch.incident.replay import rebuild  # noqa: E402

LABEL = ("SIMULATED BENCHMARK: demo world DEMO-1, scripted operator (reviews with simulator ground truth after "
         "{review}s) and scripted responder (acknowledges after {ack}s). Measures system latencies and counts, not "
         "human performance or real-video detection.")
MISSING_PERSON = "volunteer"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--review-delay", type=float, default=3.0)
    ap.add_argument("--ack-delay", type=float, default=2.0)
    args = ap.parse_args()

    app = create_app()
    rt = app.state.rt
    with TestClient(app) as c:
        tok = rt.cfg.secrets.overwatch_token
        params = {"token": tok} if tok else {}

        def post(url, body=None):
            r = c.post(url, json=body, params=params)
            return r

        inc = post("/api/incidents", {"title": "Benchmark: training area A", "mission_mode": "search",
                                      "scenario_id": "DEMO-1"}).json()
        iid = inc["id"]
        seen_at: dict[str, float] = {}
        alert_seen: dict[str, float] = {}
        decisions = {"confirmed": 0, "rejected": 0, "approved": 0, "acknowledged": 0}
        t_end = time.monotonic() + args.seconds
        while time.monotonic() < t_end:
            now = time.monotonic()
            for v in c.get(f"/api/incidents/{iid}/observations").json()["items"]:
                o = v["observation"]
                if v["review_status"] != "pending" or o["provenance"] == "manual" or o["category"] != "person":
                    continue
                seen_at.setdefault(o["id"], now)
                if now - seen_at[o["id"]] < args.review_delay:
                    continue
                truth = (o["attributes"] or {}).get("sim_truth") or {}
                status = "confirmed" if truth.get("actor_id") == MISSING_PERSON else "rejected"
                post(f"/api/observations/{o['id']}/review", {"status": status, "reviewer": "benchmark-operator",
                                                           "note": "scripted with simulator ground truth"})
                decisions[status] += 1
            for a in c.get(f"/api/incidents/{iid}/actions").json()["items"]:
                if a["action_type"] == "notify_responder" and a["approval_status"] == "pending":
                    if post(f"/api/actions/{a['id']}/approve", {"by": "benchmark-operator"}).status_code == 200:
                        decisions["approved"] += 1
            for a in c.get(f"/api/incidents/{iid}/alerts").json()["items"]:
                if a["acknowledged_at"] or not a["source_observation_ids"]:
                    continue  # pipeline proximity alerts are acknowledged by the responder scenario itself
                alert_seen.setdefault(a["id"], now)
                if now - alert_seen[a["id"]] >= args.ack_delay:
                    post(f"/api/alerts/{a['id']}/acknowledge", {"by": "benchmark-responder"})
                    decisions["acknowledged"] += 1
            time.sleep(0.25)

        live = c.get(f"/api/incidents/{iid}/metrics").json()
        replayed = compute_metrics(rebuild(rt.incidents.store.events(iid, limit=10**9)), iid)
        keys = ("observations", "candidates", "detection_to_review", "confirmation_to_alert", "alert_ack_latency",
                "alerts", "tracks", "localization_error")
        mismatched = [k for k in keys if live[k] != replayed[k]]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = ROOT / "evaluation" / "benchmarks" / stamp
    out.mkdir(parents=True, exist_ok=True)
    label = LABEL.format(review=args.review_delay, ack=args.ack_delay)
    report = {"label": label, "provenance": "simulated", "incident_id": iid, "seconds": args.seconds,
              "scripted_actions": decisions, "metrics": live, "replayed_from_log_matches": not mismatched,
              "mismatched": mismatched}
    (out / "incident_benchmark.json").write_text(json.dumps(report, indent=2, default=str))
    m = live
    lines = [f"# Incident benchmark ({stamp})", "", f"**{label}**", "",
             f"- Duration: {args.seconds:.0f} s wall clock; scripted actions: {decisions}",
             f"- Candidates: {m['candidates']}",
             f"- Observations with a verified position: {m['observations']['with_valid_world_position']} of "
             f"{m['observations']['total']}",
             f"- Detection to review: {m['detection_to_review']} (includes the scripted {args.review_delay:.0f} s delay)",
             f"- Confirmation to alert: {m['confirmation_to_alert']} (includes the next rules cycle and approval)",
             f"- Alert acknowledgment latency: {m['alert_ack_latency']} (scripted {args.ack_delay:.0f} s delay)",
             f"- Tracks: {m['tracks']}",
             f"- Localization error: {m['localization_error'] or m['localization_error_note']}",
             f"- Metrics recomputed from the event log alone match the live values: {not mismatched}"
             + (f" (mismatched: {mismatched})" if mismatched else ""), ""]
    (out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"report: {out}")
    return 0 if not mismatched else 1


if __name__ == "__main__":
    raise SystemExit(main())
