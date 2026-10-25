"""Deterministic pixels -> existing HeadlessRunner -> incident API -> review -> guidance -> ACK.

The human actions below are explicit scripted acceptance actions, not model decisions.
Run from the repository root: PYTHONPATH=backend backend/.venv/bin/python backend/scripts/evaluate_sar.py
"""
import argparse
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import Secrets, load_config
from overwatch.evaluation.scenario_runner import HeadlessRunner


def evaluate(output: Path | None = None) -> dict:
    os.environ.setdefault("OVERWATCH_DB", ":memory:")
    cfg = load_config()
    cfg.session.mode, cfg.session.scenario = "SIMULATED", "SAR1"
    cfg.decisions.primary, cfg.decisions.shadow, cfg.decisions.allow_mock = "rules", [], False
    cfg.secrets = Secrets()
    cfg.autonomy.allow_real_flight = False
    runner = HeadlessRunner(cfg, "SAR1", engine="rules")
    app = create_app(cfg)
    rt = app.state.rt
    # Reuse the production incident/API services with the existing synchronous simulation runner.
    rt.pipeline, rt.sim, rt.clock = runner.pipeline, runner.sim, runner.clock
    rt.session_id = runner.pipeline.session_id
    client = TestClient(app)  # no paced background tasks; runner controls simulation time
    inc = client.post("/api/incidents", json={"title": "SAR acceptance", "scenario_id": "SAR1", "mission_mode": "search"}).json()
    iid = inc["id"]
    found, reviewed, alert, guidance = [], None, None, None
    last = [-1e12]

    def step(r, ts):
        nonlocal found, reviewed, alert, guidance
        if ts - last[0] < 200:
            return
        last[0] = ts
        rt.incidents.sync_tracks()
        rt.incidents.run_missions()
        for msg in r.pipeline.drain_outbox():
            rt.incidents.on_outbox(msg)
        items = client.get(f"/api/incidents/{iid}/sar").json()["items"]
        if items and reviewed is None:
            found = items
            item = items[0]
            oid = item["observation"]["id"]
            response = client.post(f"/api/incidents/{iid}/sar/{oid}/review", json={
                "status": "confirmed", "dispatch": True, "reviewer": "scripted acceptance operator",
                "session_id": rt.pipeline.session_id})
            assert response.status_code == 200, response.text
            reviewed = response.json()["items"][0]
            guidance, alert = reviewed["guidance"], reviewed["dispatch"]
            assert alert is not None
            assert client.post(f"/api/alerts/{alert['id']}/delivered").status_code == 200
            assert client.post(f"/api/alerts/{alert['id']}/acknowledge", json={"by": "scripted responder"}).status_code == 200

    result = runner.run(duration_s=8, on_step=step)
    items = client.get(f"/api/incidents/{iid}/sar").json()["items"]
    events = rt.incidents.store.events(iid)
    replayed = client.get(f"/api/incidents/{iid}/events").json()["items"]
    criteria = {
        "detected_assistance_candidate": bool(found),
        "real_pixel_detector": bool(found and found[0]["observation"]["detector"] == "sim-blob-v1"),
        "evidence_saved": bool(found and found[0]["observation"]["evidence_frame_reference"]),
        "normal_person_not_flagged": all(i["observation"]["attributes"].get("sim_truth", {}).get("actor_id") != "dry-fixture" for i in items),
        "responder_excluded": all(i["observation"]["track_id"] != rt.pipeline.locks["responder"].track_id for i in items),
        "no_duplicate_assistance_candidates": len(items) == 1,
        "human_review_recorded": any(e.kind == "sar_reviewed" for e in events),
        "guidance_is_simulated": bool(guidance and guidance["mode"] == "simulated_navigation"),
        "distance_and_bearing_derived": bool(guidance and guidance.get("distance_m", 0) > 0 and "bearing_deg" in guidance),
        "route_stops_before_flood": bool(guidance and guidance["route"]["status"] == "approach_only"
                                        and guidance["route"]["max_water_depth_m"] <= cfg.sar.route_max_depth_m),
        "alert_delivered_and_acknowledged": bool(items and items[0]["dispatch"] and items[0]["dispatch"]["acknowledged_at"]),
        "audit_backlog_available": any(e["kind"] == "alert_acknowledged" for e in replayed),
        "no_physical_flight": not cfg.autonomy.allow_real_flight and rt.pipeline.autonomy.target_is_sim,
    }
    summary = {"scenario": "SAR1", "engine": "rules", "simulated_seconds": result.duration_s,
               "criteria": criteria, "passed": all(criteria.values()), "guidance": guidance,
               "candidate_count": len(items), "detector_frames": rt.pipeline.counters["drone"].processed,
               "note": "synthetic sensors; scripted human review; no hardware verification"}
    if output:
        output.mkdir(parents=True, exist_ok=True)
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        (output / "incident-events.json").write_text(json.dumps(replayed, indent=2))
    rt.executor.shutdown(wait=False)
    rt.incidents.store.close()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = evaluate(args.output)
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["passed"] else 1)
