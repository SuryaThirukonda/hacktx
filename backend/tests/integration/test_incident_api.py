import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app


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


@pytest.fixture()
def incident(client):
    r = post(client, "/api/incidents", {"title": "Training area A", "mission_mode": "search"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["mode"] in ("SIMULATED", "LIVE", "REPLAY")


def test_create_and_get_incident(client, incident):
    r = client.get(f"/api/incidents/{incident['id']}")
    body = r.json()
    assert body["title"] == "Training area A" and body["current"] is True
    assert body["provenance"] == "simulated"  # the default runtime is the simulator
    assert client.app_rt.pipeline.incident.incident_id == incident["id"]  # pipeline incident started too
    assert any(i["id"] == incident["id"] for i in client.get("/api/incidents").json()["items"])


def test_observation_ingest_review_and_unverified_location(client, incident):
    iid = incident["id"]
    r = post(client, f"/api/incidents/{iid}/observations",
             {"category": "person", "label": "possible person", "sensor_source_id": "drone",
              "bounding_box": {"frame": "image:drone", "x0": 10, "y0": 10, "x1": 40, "y1": 90},
              "provenance": "simulated"})
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    assert r.json()["world_position"] is None
    items = client.get(f"/api/incidents/{iid}/observations").json()["items"]
    assert items[0]["review_status"] == "pending"
    r = post(client, f"/api/observations/{oid}/review", {"status": "confirmed", "reviewer": "op1"})
    assert r.json()["review_status"] == "confirmed" and r.json()["reviewer"] == "op1"
    assert client.get(f"/api/incidents/{iid}/observations?status=confirmed").json()["items"][0]["observation"]["id"] == oid


def test_structured_errors(client, incident):
    r = client.get("/api/incidents/INC-missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = post(client, f"/api/incidents/{incident['id']}/observations",
             {"category": "person", "label": "x", "horizontal_uncertainty_m": 2})  # uncertainty without a position
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = post(client, f"/api/incidents/{incident['id']}/observations",
             {"category": "person", "label": "x", "world_position": {"frame": f"enu:{incident['id']}", "x": 1, "y": 2},
              "horizontal_uncertainty_m": 2, "position_estimation_method": "manual", "provenance": "simulated"})
    assert r.status_code == 422  # manual positions must carry manual provenance


def test_mission_transitions_are_logged(client, incident):
    iid = incident["id"]
    for m in ("structure", "follow", "search"):
        r = post(client, f"/api/incidents/{iid}/mission", {"mission": m})
        assert r.json()["mission_mode"] == m
    r = post(client, f"/api/incidents/{iid}/mission", {"mission": "pursuit"})
    assert r.status_code == 422
    kinds = [e["kind"] for e in client.get(f"/api/incidents/{iid}/events").json()["items"]]
    assert kinds.count("mission_changed") == 3


def test_alert_create_and_acknowledge(client, incident):
    iid = incident["id"]
    a = post(client, f"/api/incidents/{iid}/alerts", {"message": "Confirmed location marker, north side of structure."}).json()
    assert a["status"] == "pending"
    r = post(client, f"/api/alerts/{a['id']}/acknowledge", {"by": "responder-1"})
    assert r.json()["status"] == "acknowledged" and r.json()["acknowledged_by"] == "responder-1"
    m = client.get(f"/api/incidents/{iid}/metrics").json()
    assert m["alerts"]["acknowledged"] >= 1 and m["alert_ack_latency"]["n"] >= 1


def test_pipeline_alerts_are_mirrored_and_ackable(client, incident):
    svc = client.app_rt.incidents
    svc.on_outbox({"type": "alert", "alert_id": "AL-test-1", "severity": "local_proximity", "text": "Obstacle close"})
    sid = svc.store_alert_id("AL-test-1")
    assert sid.endswith(".AL-test-1") and sid != "AL-test-1"  # namespaced by session: pipeline ids restart
    a = [x for x in client.get(f"/api/incidents/{incident['id']}/alerts").json()["items"] if x["id"] == sid][0]
    assert a["severity"] == "critical" and a["status"] == "delivered"
    assert post(client, f"/api/alerts/{sid}/acknowledge").status_code == 200  # the namespaced id is URL-safe
    a = [x for x in client.get(f"/api/incidents/{incident['id']}/alerts").json()["items"] if x["id"] == sid][0]
    assert a["status"] == "acknowledged"


def test_scene_anchor_frames(client, incident):
    iid = incident["id"]
    r = post(client, f"/api/incidents/{iid}/scene/anchors",
             {"name": "Door 1", "type": "door", "position": {"frame": f"enu:{iid}", "x": 5, "y": 2}})
    assert r.status_code == 200
    r = post(client, f"/api/incidents/{iid}/scene/anchors",
             {"name": "bad", "type": "door", "position": {"frame": "nonsense", "x": 5, "y": 2}})
    assert r.status_code == 422
    assert client.get(f"/api/incidents/{iid}/scene").json()["anchors"][0]["name"] == "Door 1"


def test_responder_position_and_replay_controls(client, incident):
    iid = incident["id"]
    r = client.post(f"/api/incidents/{iid}/responder-position", json={"lat": 30.28, "lon": -97.73, "accuracy_m": 6})
    assert r.json()["lat"] == 30.28
    assert post(client, f"/api/incidents/{iid}/replay/pause").json()["paused"] is True
    assert post(client, f"/api/incidents/{iid}/replay/start").json()["paused"] is False


def test_websocket_delivers_events_in_order(client, incident):
    iid = incident["id"]
    with client.websocket_connect(f"/ws/incidents/{iid}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "backlog" and hello["items"][0]["kind"] == "incident_created"
        last = hello["items"][-1]["seq"]
        for i in range(3):
            post(client, f"/api/incidents/{iid}/observations", {"category": "opening", "label": f"door {i}"})
        got = [ws.receive_json() for _ in range(3)]
        assert [g["kind"] for g in got] == ["observation"] * 3
        seqs = [g["seq"] for g in got]
        assert seqs == sorted(seqs) and seqs[0] > last
        assert [g["payload"]["observation"]["label"] for g in got] == ["door 0", "door 1", "door 2"]
    # reconnect with ?after= only replays what was missed
    with client.websocket_connect(f"/ws/incidents/{iid}?after={seqs[1]}") as ws:
        hello = ws.receive_json()
        assert [e["seq"] for e in hello["items"]] == [seqs[2]]


def test_source_switch_is_honest_in_simulated_mode(client, incident):
    iid = incident["id"]
    lst = client.get(f"/api/incidents/{iid}/sources").json()
    assert lst["active"] == "simulated"
    st = {s["id"]: s["connection_status"] for s in lst["items"]}
    assert st["simulated"] == "connected" and st["dji_mini2"] == "not_configured"
    r = post(client, f"/api/incidents/{iid}/sources", {"source": "file", "value": "/tmp/clip.mp4"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "source_unavailable"
    kinds = [e["kind"] for e in client.get(f"/api/incidents/{iid}/events").json()["items"]]
    assert "error" in kinds


def test_usecases_list_honest_status(client):
    items = {u["id"]: u for u in client.get("/api/usecases").json()["items"]}
    assert items["training"]["status"] == "ready" and items["training"]["scenarios"][0]["id"] == "DEMO-1"
    for wid, first in (("pursuit", "PP0"), ("sar", "SR1"), ("flood", "FR1"), ("fire", "FF1")):
        assert items[wid]["status"] == "ready" and items[wid]["scenarios"][0]["id"] == first
        assert items[wid]["not_built"]  # what is not built is said, not hidden


def test_flag_track_creates_candidate_with_evidence(client, incident):
    import time

    iid = incident["id"]
    tid = None
    deadline = time.monotonic() + 15
    while tid is None and time.monotonic() < deadline:
        tracks = [t for t in client.get("/api/tracks").json() if t["state"] == "CONFIRMED" and t["box_xyxy_px"]]
        tid = tracks[0]["track_id"] if tracks else None
        time.sleep(0.3)
    assert tid, "simulator produced no confirmed track"
    r = post(client, f"/api/incidents/{iid}/observations/from-track", {"track_id": tid})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["provenance"] == "simulated" and o["bounding_box"]["frame"] == "image:drone"
    assert 0 <= o["confidence"] <= 1
    assert o["attributes"]["confidence_kind"] == "uncalibrated_model_score"  # the simulated detector is not calibrated
    if o["world_position"] is not None:
        assert o["horizontal_uncertainty_m"] is not None and o["position_estimation_method"] == "ground_plane"
    name = o["evidence_frame_reference"].split("/")[1].split("#")[0]
    img = client.get(f"/api/evidence/{name}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get("/api/evidence/..%2Fsecrets").status_code == 404
    r = post(client, f"/api/incidents/{iid}/observations/from-track", {"track_id": "drone:999:e9"})
    assert r.status_code == 400


def test_simulated_responder_position_and_brief(client, incident):
    iid = incident["id"]
    r = client.post(f"/api/incidents/{iid}/responder-position", json={"source": "simulated"})
    assert r.status_code == 200, r.text
    assert r.json()["enu"]["frame"] == f"enu:{iid}" and r.json()["heading_accuracy_deg"] == 5.0
    o = post(client, f"/api/incidents/{iid}/observations",
             {"category": "supply", "label": "supply cache", "provenance": "manual",
              "world_position": {"frame": f"enu:{iid}", "x": -7.5, "y": 10.5}, "horizontal_uncertainty_m": 2.0,
              "position_estimation_method": "manual"}).json()
    b = client.get(f"/api/incidents/{iid}/responder-brief").json()
    assert b["responder"]["grounded"] and b["responder"]["heading_trusted"]
    t = [x for x in b["targets"] if x["observation_id"] == o["id"]][0]
    assert t["grounded"] and t["range_m"] >= 0 and 1 <= t["clock"] <= 12 and t["uncertainty_m"] >= 2.0


def test_perception_status_and_track_history(client, incident):
    import time

    p = client.get("/api/perception").json()
    names = {d["name"]: d for d in p["detectors"]}
    assert names["sim_blob"]["installed"] and "AGPL" in names["ultralytics"]["license"]
    assert "not mean" in p["note"]
    iid = incident["id"]
    deadline = time.monotonic() + 15
    items = []
    while not items and time.monotonic() < deadline:
        client.app_rt.incidents.sync_tracks()
        items = client.get(f"/api/incidents/{iid}/tracks").json()["items"]
        time.sleep(0.3)
    assert items, "no person track was persisted"
    t = items[0]
    assert t["identity_status"] == "unverified" and t["provenance"] == "simulated" and t["tracking_status"] in ("active", "lost")
    kinds = {e["kind"] for e in client.get(f"/api/incidents/{iid}/events").json()["items"]}
    assert "track_started" in kinds


def test_georef_and_observation_location(client, incident):
    iid = incident["id"]
    inc = client.get(f"/api/incidents/{iid}").json()
    assert "not surveyed" in inc["coordinate_reference"]["note"]  # the configured stand-in is labelled
    o = post(client, f"/api/incidents/{iid}/observations", {"category": "person", "label": "x"}).json()
    assert client.get(f"/api/observations/{o['id']}/location").json() == {"verified": False, "text": "location not yet verified"}
    m = post(client, f"/api/incidents/{iid}/observations",
             {"category": "other", "label": "marker", "provenance": "manual",
              "world_position": {"frame": f"enu:{iid}", "x": 0, "y": 111.2}, "horizontal_uncertainty_m": 2,
              "position_estimation_method": "manual"}).json()
    r = post(client, f"/api/incidents/{iid}/georef", {"origin_lat": 30.0, "origin_lon": -97.0, "note": "surveyed"})
    assert r.json()["coordinate_reference"]["origin_lat"] == 30.0
    loc = client.get(f"/api/observations/{m['id']}/location").json()
    assert loc["verified"] and abs(loc["wgs84"]["lat"] - 30.001) < 2e-4 and abs(loc["wgs84"]["lon"] + 97.0) < 1e-6
    assert post(client, f"/api/incidents/{iid}/georef", {"origin_lat": 95, "origin_lon": 0}).status_code == 422
