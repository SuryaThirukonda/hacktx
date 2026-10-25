"""Base-launch flood/fire/forest -> measured beacon -> human API review -> crew -> RTL.

Synthetic pixel detection and explicit scripted people. No cloud or aircraft hardware.
"""
import argparse
import json
import math
import os
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from overwatch.api.app import create_app
from overwatch.config import Config, REPO_ROOT, Secrets
from overwatch.evaluation.scenario_runner import HeadlessRunner


def evaluate(sid, output):
    os.environ["OVERWATCH_DB"] = ":memory:"
    cfg = Config.model_validate(yaml.safe_load((REPO_ROOT / "config/default.yaml").read_text()))
    cfg.session.scenario = sid
    cfg.decisions.primary, cfg.decisions.shadow = "rules", []
    cfg.secrets = Secrets()
    runner = HeadlessRunner(cfg, sid, engine="rules")
    app = create_app(cfg)
    rt = app.state.rt
    rt.pipeline, rt.sim, rt.clock = runner.pipeline, runner.sim, runner.clock
    rt.session_id = runner.pipeline.session_id
    client = TestClient(app)
    inc = client.post("/api/incidents", json={"title": "Simulated beacon acceptance", "scenario_id": sid,
                                             "mission_mode": "structure" if sid.startswith("FF") else "search"}).json()
    iid = inc["id"]
    last = -1e12
    evidence = {}
    def step(r, t):
        nonlocal last
        if t - last < 200:
            return
        last = t
        rt.incidents.sync_tracks()
        rt.incidents.run_missions()
        for msg in r.pipeline.drain_outbox():
            rt.incidents.on_outbox(msg)
        mission = r.pipeline.rescue
        items = client.get(f"/api/incidents/{iid}/sar").json()["items"]
        if mission.beacon and "beacon_error_m" not in evidence:
            # Evaluation truth is only used here, after the measured beacon was created.
            truth = next(a for a in r.sim.actors if a.role == "subject")
            evidence["beacon_error_m"] = math.dist(mission.beacon["xy"], truth.pos)
            evidence["crew_stationary_before_approval"] = mission.distance == 0
        if mission.phase == "REVIEW" and mission.beacon.get("observation_id") and not evidence.get("reviewed"):
            oid = mission.beacon["observation_id"]
            response = client.post(f"/api/incidents/{iid}/sar/{oid}/review", json={
                "status": "confirmed", "dispatch": True, "reviewer": "scripted simulation operator",
                "session_id": rt.pipeline.session_id})
            assert response.status_code == 200, response.text
            item = next(i for i in response.json()["items"] if i["observation"]["id"] == oid)
            alert = item["dispatch"]
            assert alert is not None
            assert client.post(f"/api/alerts/{alert['id']}/delivered").status_code == 200
            assert client.post(f"/api/alerts/{alert['id']}/acknowledge", json={"by": "scripted responder"}).status_code == 200
            evidence.update(reviewed=True, observation_id=oid, alert_id=alert["id"], guidance=item["guidance"])
    res = runner.run(85, on_step=step, stop_when=lambda r, t: r.pipeline.rescue.phase == "COMPLETE")
    mission = runner.pipeline.rescue.view()
    alerts = rt.incidents.store.list_alerts(iid)
    checks = {
        "measured_beacon": evidence.get("beacon_error_m", math.inf) < 3,
        "human_review_before_crew_movement": evidence.get("crew_stationary_before_approval", False) and evidence.get("reviewed", False),
        "beacon_linked_to_incident_observation": bool(runner.pipeline.rescue.beacon and runner.pipeline.rescue.beacon.get("observation_id")),
        "crew_arrived": mission["arrived_s"] is not None,
        "returned_to_base": mission["phase"] == "COMPLETE",
        "crew_moved": mission["crew_distance_m"] > 1,
        "no_unsafe_steps": mission["unsafe_steps"] == 0 and mission["max_water_depth_m"] <= .15,
        "delivery_ack": any(a.id == evidence.get("alert_id") and a.delivered_at and a.acknowledged_at for a in alerts),
        "survey_published": runner.sim.survey.encode(runner.sim.t_s)["seen_m2"] > 0,
        "no_physical_permission": not cfg.autonomy.allow_real_flight and runner.pipeline.autonomy.target_is_sim,
    }
    summary = dict(scenario=sid, passed=all(checks.values()), checks=checks, rescue=mission,
                   evidence=evidence, simulated_seconds=res.duration_s, wall_s=res.wall_s,
                   detector=runner.pipeline.detector.model_id)
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{sid}.json").write_text(json.dumps(summary, indent=2))
    (output / f"{sid}-events.json").write_text(json.dumps([e.model_dump(mode="json") for e in rt.incidents.store.events(iid)], indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="*", default=["FR3", "FF3", "SR5"])
    parser.add_argument("--output", type=Path, default=Path("evaluation/current/rescue"))
    args = parser.parse_args()
    summaries = [evaluate(sid, args.output) for sid in args.scenarios]
    print(json.dumps(summaries, indent=2))
    raise SystemExit(0 if all(s["passed"] for s in summaries) else 1)
