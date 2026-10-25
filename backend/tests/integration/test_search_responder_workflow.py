"""Actual rendered frames through pipeline → Search → approval → incident socket → idempotent ACK."""
import time

from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import load_config


def test_pipeline_search_to_responder_and_reconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", str(tmp_path / "incident.db"))
    cfg = load_config()
    cfg.secrets.overwatch_token = None
    app = create_app(cfg)
    with TestClient(app) as c:
        inc = c.post("/api/incidents", json={"title": "Search integration", "mission_mode": "search", "scenario_id": "DEMO-1"}).json()
        iid = inc["id"]
        deadline = time.monotonic() + 30
        candidates = []
        while time.monotonic() < deadline:
            candidates = c.get(f"/api/incidents/{iid}/observations").json()["items"]
            if candidates:
                break
            time.sleep(0.2)
        assert candidates, "real pipeline produced no Search candidates"
        v = candidates[0]
        o = v["observation"]
        assert v["review_status"] == "pending" and o["detector"] == "sim-blob-v1"
        assert 0 <= o["confidence"] <= 1 and o["sensor_source_id"] == "drone"
        assert o["bounding_box"] and o["evidence_frame_reference"]
        assert o["world_position"]["frame"] == inc["coordinate_reference"]["enu_frame"]
        assert o["horizontal_uncertainty_m"] > 0
        assert c.get(f"/api/incidents/{iid}/responder-brief").json()["targets"] == []
        oid = o["id"]
        assert c.post(f"/api/observations/{oid}/review", json={"status": "confirmed", "reviewer": "operator"}).status_code == 200
        app.state.rt.incidents.sync_tracks()
        track = next(t for t in c.get(f"/api/incidents/{iid}/tracks").json()["items"] if t["id"] == o["attributes"]["incident_track_id"])
        assert oid in track["observation_ids"]
        app.state.rt.incidents.run_missions()
        action = next(a for a in c.get(f"/api/incidents/{iid}/actions").json()["items"]
                      if a["action_type"] == "notify_responder" and a["parameters"].get("observation_id") == oid)
        with c.websocket_connect(f"/ws/incidents/{iid}") as ws:
            assert ws.receive_json()["type"] == "backlog"
            assert c.post(f"/api/actions/{action['id']}/approve", json={"by": "operator"}).status_code == 200
            alert = None
            for _ in range(100):
                ev = ws.receive_json()
                if ev["kind"] == "alert" and ev["payload"].get("source_observation_ids") == [oid]:
                    alert = ev["payload"]
                    break
            assert alert
            aid = alert["id"]
            first = c.post(f"/api/alerts/{aid}/acknowledge", json={"by": "responder"}).json()
            second = c.post(f"/api/alerts/{aid}/acknowledge", json={"by": "responder"}).json()
            assert first["acknowledged_at"] == second["acknowledged_at"]
        with c.websocket_connect(f"/ws/incidents/{iid}") as ws:
            events = ws.receive_json()["items"]
            acks = [e for e in events if e["kind"] == "alert_acknowledged" and e["payload"]["alert_id"] == aid]
            assert len(acks) == 1
        assert {"observation", "review", "alert", "alert_acknowledged"} <= {e["kind"] for e in events}


def test_same_pipeline_track_in_two_incidents_has_separate_history(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", str(tmp_path / "isolation.db"))
    cfg = load_config()
    cfg.secrets.overwatch_token = None
    with TestClient(create_app(cfg)) as c:
        first = c.post("/api/incidents", json={"title": "First", "scenario_id": "DEMO-1"}).json()
        deadline = time.monotonic() + 30
        while not c.get(f"/api/incidents/{first['id']}/tracks").json()["items"]:
            assert time.monotonic() < deadline
            time.sleep(0.2)
        c.post("/api/simulation/control", json={"action": "pause"})
        original = c.get(f"/api/incidents/{first['id']}/tracks").json()["items"]
        second = c.post("/api/incidents", json={"title": "Second", "scenario_id": "DEMO-1"}).json()
        # Same source session/track, different incident; the old incident must retain its own records.
        rt = c.app.state.rt
        rt.incidents.sync_tracks()
        newer = c.get(f"/api/incidents/{second['id']}/tracks").json()["items"]
        assert newer and all(t["incident_id"] == second["id"] for t in newer)
        assert not ({t["id"] for t in original} & {t["id"] for t in newer})
        assert c.get(f"/api/incidents/{first['id']}/tracks").json()["items"] == original
