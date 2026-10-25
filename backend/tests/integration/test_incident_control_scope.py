"""Global subject control belongs to one active incident/session and human designation."""
from collections import Counter

import numpy as np
import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import load_config
from overwatch.vision.tracker import Track, KalmanBox


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    cfg.decisions.primary = "rules"; cfg.decisions.shadow = []
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline
    c = TestClient(app)
    first = c.post("/api/incidents", json={"title": "Old"}).json()["id"]
    p.counters["drone"].last_rx_ms = rt.clock.now_ms()
    p.last_frame = np.zeros((540, 960, 3), np.uint8)
    p.raw_tracks = [Track(n, 1, "drone", KalmanBox((100*n, 100, 100*n+60, 300)), 0, 0,
                         Counter({"person": 4}), state="CONFIRMED", hits=4, conf=.95,
                         measured_box=(100*n, 100, 100*n+60, 300)) for n in (1, 2)]
    return c, rt, first, [t.track_id for t in p.raw_tracks]


@pytest.mark.parametrize("action", ["designate", "confirm", "release", "describe", "describe/confirm"])
def test_old_incident_cannot_mutate_current_lock(scene, action):
    c, rt, old, tids = scene
    current = c.post("/api/incidents", json={"title": "Current"}).json()["id"]
    assert c.post(f"/api/incidents/{current}/follow/designate", json={"track_id": tids[0]}).status_code == 200
    p = rt.pipeline
    before = (p.locks["subject"].track_id, p.flight_confirmed_track_id, p.locks["subject"].since_ms)
    r = c.post(f"/api/incidents/{old}/follow/{action}", json={"track_id": tids[1], "text": "blue shirt"})
    assert 400 <= r.status_code < 500, r.text
    assert (p.locks["subject"].track_id, p.flight_confirmed_track_id, p.locks["subject"].since_ms) == before
    rt.incidents.follow_update(old)
    assert rt.incidents.follow_tracker(old).s.designation == 0


def test_incident_switch_and_close_stop_and_invalidate_target(scene):
    c, rt, iid, tids = scene
    c.post(f"/api/incidents/{iid}/follow/designate", json={"track_id": tids[0]})
    rt.pipeline.autonomy.mode = "FOLLOW"
    newer = c.post("/api/incidents", json={"title": "Current"}).json()["id"]
    assert rt.pipeline.autonomy.mode == "MANUAL"
    assert rt.pipeline.locks["subject"].state == "UNLOCKED" and rt.pipeline.flight_confirmed_track_id is None
    c.delete("/api/incident")
    assert c.post(f"/api/incidents/{newer}/follow/designate", json={"track_id": tids[0]}).status_code == 403


def test_stale_session_designation_and_unobserved_targets_rejected(scene):
    c, rt, iid, tids = scene
    path = f"/api/incidents/{iid}/follow"
    first = c.post(path+"/designate", json={"track_id": tids[0]}).json()
    assert first["observation_id"]
    view = rt.incidents.store.view(rt.incidents.store.get_observation(first["observation_id"]))
    assert view.review_status == "confirmed" and view.reviewer == "operator"
    assert view.observation.track_id == tids[0]
    assert view.observation.attributes["session_id"] == rt.pipeline.session_id
    assert not rt.cfg.autonomy.allow_real_flight and rt.pipeline.autonomy.mode == "MANUAL"
    rt.pipeline.counters["drone"].last_rx_ms = rt.clock.now_ms()
    rt.pipeline.autonomy.mode = "FOLLOW"
    second = c.post(path+"/designate", json={"track_id": tids[1]}).json()
    assert second["designation"] > first["designation"] and rt.pipeline.autonomy.mode == "MANUAL"
    for body in ({"session_id": "old"}, {"designation": first["designation"]}):
        assert c.post(path+"/release", json=body).status_code == 400
        assert rt.pipeline.locks["subject"].track_id == tids[1]
    rt.pipeline.raw_tracks[0].matched_now = False
    assert c.post(path+"/designate", json={"track_id": tids[0]}).status_code == 409
    rt.pipeline.counters["drone"].last_rx_ms -= 5000
    assert c.post(path+"/designate", json={"track_id": tids[1]}).status_code == 409


def test_rebind_confirmation_links_new_evidence_and_needs_new_start(scene):
    c, rt, iid, tids = scene
    path = f"/api/incidents/{iid}/follow"
    first = c.post(path+"/designate", json={"track_id": tids[0]}).json()
    p = rt.pipeline
    p.locks["subject"].track_id = tids[1]; p.locks["subject"].verified = False
    p.autonomy.mode = "FOLLOW"
    rt.incidents.follow_update(iid)
    r = c.post(path+"/confirm", json={"session_id": first["session_id"], "designation": first["designation"]})
    assert r.status_code == 200, r.text
    assert r.json()["observation_id"] != first["observation_id"]
    assert p.flight_confirmed_track_id == tids[1] and p.autonomy.mode == "MANUAL"


def test_global_subject_lock_without_incident_is_forbidden(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    c = TestClient(create_app(cfg))
    assert c.post("/api/locks/subject", json={"track_id":"missing"}).status_code == 403
