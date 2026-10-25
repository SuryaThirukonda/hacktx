"""Description -> model result -> one persistent designation -> bounded aircraft follow. No external calls."""
from collections import Counter
from types import SimpleNamespace
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import load_config
from overwatch.schemas import DecisionAnswer, DecisionRecord
from overwatch.vision.tracker import Track, KalmanBox


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config()
    cfg.session.mode = "LIVE"
    cfg.autonomy.motion_profile = "camera_only"
    cfg.decisions.primary = "rules"
    cfg.decisions.shadow = []
    cfg.secrets.overwatch_token = "mark-test"
    app = create_app(cfg)
    rt = app.state.rt
    p = rt.pipeline
    client = TestClient(app)  # no background loops, clocks and decisions controlled by this test
    client.headers["Authorization"] = "Bearer mark-test"
    iid = client.post("/api/incidents", json={"title": "Clothing test", "mission_mode": "follow"}).json()["id"]
    p.counters["drone"].last_rx_ms = rt.clock.now_ms()
    p.last_frame = np.zeros((540, 960, 3), dtype=np.uint8)
    for n, colors in enumerate([("blue", "black"), ("red", "white")], 1):
        box = (100*n, 100, 100*n+60, 300)
        t = Track(n, 1, "drone", KalmanBox(box), 0, 0, Counter({"person": 4}), state="CONFIRMED", hits=4,
                  measured_box=box, conf=.95)
        p.raw_tracks.append(t)
        p.track_colors[t.track_id] = colors
    jobs = []
    def submit(job):
        job.created_ms = rt.clock.now_ms()
        jobs.append(job)
        return True
    p.decisions = SimpleNamespace(last_fp={}, submit=submit)
    return client, rt, iid, jobs


def result(rt, job, confidence=.95, engine="openai", **changes):
    rec = DecisionRecord(decision_id=uuid.uuid4().hex, task="D3_describe", engine=engine, status="success",
                         request_ts_ms=rt.clock.now_ms(), selected="A", acted=True,
                         answers=[DecisionAnswer(name="match", type="choice", choice="A", confidence=confidence,
                                                 probabilities={"A": confidence, "B": 1-confidence, "none": 0})],
                         **changes)
    rt.pipeline.apply_decision(job, [rec])
    return rec


def request(scene, text="Hey mark the guy wearing the blue tshirt and black pants"):
    client, rt, iid, jobs = scene
    rt.pipeline.counters["drone"].last_rx_ms = rt.clock.now_ms()
    response = client.post(f"/api/incidents/{iid}/person-mark", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json(), jobs[-1]


@pytest.mark.parametrize("confidence,expected", [(.9, "review"), (.95, "review"), (.899, "review"), (None, "review")])
def test_confidence_boundary_and_no_implicit_flight(scene, confidence, expected):
    client, rt, iid, _ = scene
    state, job = request(scene)
    rec = DecisionRecord(decision_id="boundary", task="D3_describe", engine="openai", status="success",
                         request_ts_ms=0, selected="A", acted=True,
                         answers=[DecisionAnswer(name="match", type="choice", choice="A", confidence=confidence)])
    rt.pipeline.apply_decision(job, [rec])
    state = client.get(f"/api/incidents/{iid}/person-mark").json()
    assert state["status"] == expected and state["confidence"] == confidence
    assert rt.pipeline.autonomy.mode == "MANUAL"
    assert (rt.pipeline.locks["subject"].state != "UNLOCKED") == (expected == "approved")
    assert rt.pipeline.describe_proposal is None  # not duplicated as a second legacy confirmation prompt


@pytest.mark.parametrize("problem", ["lookalike", "wrong_clothes", "stale_video", "expired", "lost", "operator_changed", "mock", "fallback", "unsupported"])
def test_high_confidence_never_bypasses_observations(scene, problem):
    client, rt, iid, _ = scene
    _, job = request(scene, "Mark the person with a backpack" if problem == "unsupported" else "blue shirt black pants")
    p = rt.pipeline
    if problem == "lookalike":
        p.track_colors[p.raw_tracks[1].track_id] = ("blue", "black")
    if problem == "wrong_clothes":
        p.track_colors[p.raw_tracks[0].track_id] = ("red", "white")
    if problem == "stale_video": p.counters["drone"].last_rx_ms = rt.clock.now_ms()-2000
    if problem == "expired": rt.incidents.person_mark.requests[iid]["created_ms"] -= 5000
    if problem == "lost": p.raw_tracks[0].matched_now = False
    if problem == "operator_changed": p.lock_track("subject", p.raw_tracks[1].track_id)
    result(rt, job, .99, "rules" if problem == "fallback" else "openai", mock=problem == "mock")
    state = client.get(f"/api/incidents/{iid}/person-mark").json()
    assert state["status"] != "approved"
    assert p.autonomy.mode == "MANUAL"


def test_new_request_clear_and_old_incident_discard_late_answers(scene):
    client, rt, iid, _ = scene
    _, old = request(scene)
    latest, new = request(scene)
    result(rt, old)
    assert rt.incidents.person_mark.requests[iid]["status"] == "pending"
    client.post(f"/api/incidents/{iid}/person-mark/release")
    result(rt, new)
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    _, pending = request(scene)
    client.post("/api/incidents", json={"title": "New incident"})
    result(rt, pending)
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"


def test_manual_review_preview_release_and_auth(scene):
    client, rt, iid, _ = scene
    initial, job = request(scene)
    result(rt, job, .8)
    base = f"/api/incidents/{iid}/person-mark"
    assert client.get(base+"/preview", params={"request_id": initial["request_id"]}).status_code == 200
    assert client.get(base+"/preview", params={"request_id": "old"}).status_code == 404
    approved = client.post(base+"/confirm", json={"request_id": initial["request_id"]})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.post(base+"/release").json()["status"] == "idle"
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    client.headers.pop("Authorization")
    assert client.post(base, json={"text": "blue shirt"}).status_code == 401
    assert client.get(base+"/preview", params={"request_id": initial["request_id"]}).status_code == 401


def test_mark_to_follow_requires_real_gate_and_rc_priority(scene):
    client, rt, iid, _ = scene
    p = rt.pipeline
    state, job = request(scene)
    result(rt, job)
    assert client.post(f"/api/incidents/{iid}/person-mark/confirm", json={"request_id": state["request_id"]}).status_code == 200
    base = f"/api/incidents/{iid}/person-mark/follow"
    body = {"request_id": state["request_id"]}
    assert client.post(base, json=body).status_code == 409
    class Bridge:
        is_sim = True
        control_capable = True
        control_inhibited = False
        def __init__(self): self.messages = []
        def connected(self, now): return True
        def send(self, command): self.messages.append(command)
    p.autonomy.bridge = Bridge()
    p.counters["telemetry"].last_rx_ms = rt.clock.now_ms()
    response = client.post(base, json=body)
    assert response.status_code == 200, response.text
    assert p.autonomy.mode == "FOLLOW"
    p.autonomy.tick(p._autonomy_inputs(rt.clock.now_ms()))
    sent = [m for m in p.autonomy.bridge.messages if m.type == "vstick"]
    assert sent and sent[-1].payload["yaw_rate_dps"] < 0
    assert sent[-1].payload["forward_mps"] == sent[-1].payload["right_mps"] == sent[-1].payload["up_mps"] == 0
    p.autonomy.bridge.control_inhibited = True
    assert client.post(base, json=body).status_code == 409
    p.autonomy.tick(p._autonomy_inputs(rt.clock.now_ms()))
    assert p.autonomy.mode == "MANUAL"


def test_voice_upload_transcribes_but_does_not_execute_unreviewed_audio(scene, monkeypatch):
    client, rt, iid, _ = scene
    import overwatch.voice.transcription as speech
    async def fake_transcribe(cfg, audio, format):
        assert audio == b"audio-fixture" and format == "webm"
        return "mark the blue shirt black pants"
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    base = f"/api/incidents/{iid}/person-mark"
    r = client.post(base+"/transcribe?format=webm", content=b"audio-fixture")
    assert r.status_code == 200 and "blue shirt" in r.json()["text"]
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    assert client.post(base+"/transcribe?format=webm", content=b"x"*(4*1024*1024+1)).status_code == 413


def test_physical_rebinding_requires_confirmed_track_before_movement(scene):
    client, rt, iid, _ = scene
    p = rt.pipeline
    state, job = request(scene)
    result(rt, job)
    assert client.post(f"/api/incidents/{iid}/person-mark/confirm", json={"request_id": state["request_id"]}).status_code == 200
    original = p.flight_confirmed_track_id
    p.autonomy.bridge = SimpleNamespace(is_sim=False)
    other = p.raw_tracks[1].track_id
    p.locks["subject"].track_id = other
    p.locks["subject"].verified = True  # appearance/D2 confirmation cannot authorize physical pursuit
    assert p.flight_confirmed_track_id == original
    assert not p._autonomy_inputs(rt.clock.now_ms()).subject_verified
    p.confirm_candidate(other)
    assert p._autonomy_inputs(rt.clock.now_ms()).subject_verified


def test_new_request_replaces_previous_mark_and_extra_details_do_not_auto_approve(scene):
    client, rt, iid, _ = scene
    state, first = request(scene)
    result(rt, first)
    assert not rt.pipeline.locks["subject"].active
    assert client.post(f"/api/incidents/{iid}/person-mark/confirm", json={"request_id": state["request_id"]}).status_code == 200
    assert rt.pipeline.locks["subject"].active
    _, next_job = request(scene, "blue shirt black pants with a hat")
    assert not rt.pipeline.locks["subject"].active
    result(rt, next_job, .99)
    assert client.get(f"/api/incidents/{iid}/person-mark").json()["status"] == "review"
    assert not rt.pipeline.locks["subject"].active


def test_high_confidence_proposal_creates_no_review_or_flight_binding(scene):
    client, rt, iid, _ = scene
    state, job = request(scene)
    result(rt, job, .999)
    assert client.get(f"/api/incidents/{iid}/person-mark").json()["status"] == "review"
    assert rt.incidents.store.list_observations(iid) == []
    assert rt.pipeline.flight_confirmed_track_id is None
    approved = client.post(f"/api/incidents/{iid}/person-mark/confirm", json={"request_id": state["request_id"]})
    assert approved.status_code == 200 and not approved.json()["auto_approved"]
    views = rt.incidents.store.list_observations(iid)
    assert len(views) == 1 and views[0].review_status == "confirmed"
    assert not rt.cfg.autonomy.allow_real_flight and rt.pipeline.autonomy.mode == "MANUAL"
