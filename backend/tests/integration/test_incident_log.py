"""Phase 11: every listed event kind is logged, metrics rebuilt from the log alone equal the live values, and
localization error is reported only where ground truth exists."""

import time

import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.incident.metrics import compute_metrics
from overwatch.incident.replay import rebuild


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        tok = app.state.rt.cfg.secrets.overwatch_token
        c.params = {"token": tok} if tok else {}  # type: ignore[assignment]
        c.app_rt = app.state.rt  # type: ignore[attr-defined]
        yield c


def post(c, url, json=None):
    return c.post(url, json=json, params=c.params)


def confirmed_track(client, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tracks = [t for t in client.get("/api/tracks").json()
                  if t["state"] == "CONFIRMED" and t["box_xyxy_px"] and t["class_name"] == "person"]
        if tracks:
            return tracks[0]["track_id"]
        time.sleep(0.3)
    return None


def test_log_covers_event_kinds_and_metrics_replay_from_log(client):
    svc = client.app_rt.incidents
    inc = post(client, "/api/incidents", {"title": "log", "mission_mode": "search", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    frame = inc["coordinate_reference"]["enu_frame"]

    tid = confirmed_track(client)
    assert tid, "simulator produced no confirmed track"
    flagged = post(client, f"/api/incidents/{iid}/observations/from-track", {"track_id": tid}).json()
    manual = post(client, f"/api/incidents/{iid}/observations",
                  {"category": "person", "label": "possible person", "provenance": "simulated", "sensor_source_id": "drone",
                   "world_position": {"frame": frame, "x": 1.0, "y": 2.0}, "horizontal_uncertainty_m": 2.0,
                   "position_estimation_method": "ground_plane"}).json()
    post(client, f"/api/observations/{flagged['id']}/review", {"status": "confirmed", "reviewer": "op1"})
    post(client, f"/api/observations/{manual['id']}/review", {"status": "rejected", "reviewer": "op1"})
    a = post(client, f"/api/incidents/{iid}/alerts", {"severity": "attention", "message": "possible person, confirmed",
                                                      "source_observation_ids": [flagged["id"]]})
    assert a.status_code == 200, a.text
    a = a.json()
    post(client, f"/api/alerts/{a['id']}/delivered")
    post(client, f"/api/alerts/{a['id']}/acknowledge", {"by": "responder"})
    post(client, f"/api/incidents/{iid}/mission", {"mission": "structure"})
    post(client, "/api/decisions/engine", {"primary": "rules"})  # an operator override
    for _ in range(4):
        svc.sync_tracks()
        svc.run_missions()
        time.sleep(0.3)

    kinds = {e["kind"] for e in client.get(f"/api/incidents/{iid}/events?limit=10000").json()["items"]}
    for k in ("incident_created", "sensor_status", "observation", "review", "track_started", "mission_changed",
              "alert", "alert_delivered", "alert_acknowledged", "decision_proposal", "operator_command"):
        assert k in kinds, f"{k} not logged (have {sorted(kinds)})"
    cmds = [e for e in client.get(f"/api/incidents/{iid}/events?limit=10000").json()["items"]
            if e["kind"] == "operator_command"]
    assert any(c["payload"]["path"] == "/api/decisions/engine" and c["payload"]["override"] for c in cmds)

    live = client.get(f"/api/incidents/{iid}/metrics").json()
    replayed = compute_metrics(rebuild(svc.store.events(iid, limit=10**9)), iid)
    for key in ("observations", "candidates", "detection_to_review", "confirmation_to_alert", "alert_ack_latency",
                "alerts", "tracks", "localization_error"):
        assert replayed[key] == live[key], key
    # the two reviewed here, plus whatever Search flagged on its own meanwhile (pending): the simulated people are
    # detected sooner now that they are drawn in detail
    assert live["candidates"]["confirmed"] == 1 and live["candidates"]["rejected"] == 1
    assert live["candidates"]["total"] == 2 + live["candidates"]["pending"]
    assert live["alert_ack_latency"]["n"] == 1 and live["confirmation_to_alert"]["n"] == 1
    for key in ("observations", "candidates", "detection_to_review", "confirmation_to_alert", "alert_ack_latency",
                "tracks", "localization_error"):
        assert live["sources"][key], f"metric {key} shown without its source"
    post(client, "/api/decisions/engine", {"primary": "openai"})


def test_localization_error_only_with_ground_truth(client):
    inc = post(client, "/api/incidents", {"title": "loc", "mission_mode": "search", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    m = client.get(f"/api/incidents/{iid}/metrics").json()
    assert m["localization_error"] is None and m["localization_error_note"]
    tid = confirmed_track(client)
    o = post(client, f"/api/incidents/{iid}/observations/from-track", {"track_id": tid}).json()
    truth = o["attributes"].get("sim_truth")
    assert truth and truth["source"].startswith("simulator ground truth")
    m = client.get(f"/api/incidents/{iid}/metrics").json()
    if o["world_position"] is not None:
        assert m["localization_error"]["n"] == 1 and m["localization_error"]["median_m"] < 5.0
        assert "ground truth" in m["localization_error"]["source"]
    else:
        assert m["localization_error"] is None
