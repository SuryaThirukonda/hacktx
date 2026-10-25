import numpy as np
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.clock import SimClock
from overwatch.config import Secrets, load_config
from overwatch.incident.replay import rebuild
from overwatch.sources.base import FramePacket
from overwatch.vision.detectors import Detection


class Detector:
    model_id = "controlled-measurements"
    valid_on = "any"
    boxes = [(160., 100., 220., 300.), (400., 100., 460., 300.)]
    def detect(self, image):
        return [Detection(b, .95) for b in self.boxes]


def setup_scene(monkeypatch, mode="SIMULATED", scenario="S01"):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config()
    cfg.session.mode, cfg.session.scenario = mode, scenario
    cfg.secrets = Secrets()
    cfg.decisions.primary, cfg.decisions.shadow = "rules", []
    cfg.perception.cmc.enabled = True
    app = create_app(cfg)
    rt, client = app.state.rt, TestClient(app)
    p = rt.pipeline
    # Fixed camera in this controlled measurement fixture; actual CMC is exercised by rendered scenarios.
    p.cmc.estimate = lambda *_: SimpleNamespace(ok=True, H=np.eye(3), inliers=40, shift_px=0.0)
    rt.clock = p.clock = SimClock()
    iid = client.post("/api/incidents", json={"title": "Continuity test", "scenario_id": scenario if mode == "SIMULATED" else None}).json()["id"]
    p.detector, p._detector_prov = Detector(), ("SYNTHETIC" if mode == "SIMULATED" else "LIVE", cfg.perception.detector)
    image = np.full((540, 960, 3), 100, np.uint8)
    image[100:220, 160:220] = (20, 20, 220)
    image[220:300, 160:220] = (20, 20, 20)
    image[100:220, 400:460] = (220, 40, 20)
    image[220:300, 400:460] = (20, 20, 20)
    px = [(0, 0), (960, 0), (960, 540), (0, 540), (480, 270)]
    p.calib.set_manual(px, [(x/20+10, y/20) for x, y in px], [False]*4+[True], 0)
    def frame(n, provenance=None):
        rt.clock.set_ms(n*100)
        p.process_drone_frame(FramePacket("drone", n, image, n*100, n*100,
                                          provenance or ("SYNTHETIC" if mode == "SIMULATED" else "LIVE")))
    for n in range(1, 7):
        frame(n)
    return client, rt, iid, frame


def test_unique_clothing_auto_selects_only_after_distinct_frames(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    response = c.post(f"/api/incidents/{iid}/target/search", json={"torso_color": "red"})
    assert response.status_code == 200
    for _ in range(8):
        rt.incidents.targets.update()
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    for n in (7, 8):
        frame(n)
        rt.incidents.targets.update()
    state = c.get(f"/api/incidents/{iid}/target").json()
    assert state["search"]["auto_approved"] and state["subject"]["state"] == "VISIBLE"
    assert rt.pipeline.autonomy.mode == "MANUAL" and not rt.cfg.autonomy.allow_real_flight
    assert not rt.incidents.store.list_alerts(iid)
    assert not [e for e in rt.incidents.store.events(iid) if e.kind == "review"]


@pytest.mark.parametrize("problem", ["similar_clothing", "stale", "quality", "recorded", "unsupported"])
def test_automatic_acquisition_refuses_ambiguous_or_unsupported_evidence(monkeypatch, problem):
    c, rt, iid, frame = setup_scene(monkeypatch)
    text = "red shirt and a hat" if problem == "unsupported" else "red shirt"
    c.post(f"/api/incidents/{iid}/target/search", json={"description": text})
    for n in range(7, 12):
        frame(n)
        if problem == "similar_clothing":
            rt.pipeline.track_colors[rt.pipeline.raw_tracks[1].track_id] = ("red", "black")
        if problem == "stale": rt.pipeline.counters["drone"].last_rx_ms -= 2000
        if problem == "quality": rt.pipeline.raw_tracks[0].conf = .1; rt.pipeline.last_detections = []
        if problem == "recorded": rt.pipeline.counters["drone"].provenance = "RECORDED"
        rt.incidents.targets.update()
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    assert not rt.cfg.autonomy.allow_real_flight


def test_live_candidate_needs_human_binding_and_never_sends_commands(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch, "LIVE")
    commands = []
    class PhysicalBridge:
        is_sim = False
        control_capable = True
        def connected(self, now): return True
        def send(self, command): commands.append(command)
    rt.pipeline.autonomy.bridge = PhysicalBridge()
    request = c.post(f"/api/incidents/{iid}/target/search", json={"description": "red shirt"}).json()
    for n in range(7, 12): frame(n); rt.incidents.targets.update()
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    state = c.get(f"/api/incidents/{iid}/target").json()
    tid = state["search"]["candidates"][0]["track_id"]
    response = c.post(f"/api/incidents/{iid}/target/confirm", json={"request_id": request["request_id"], "track_id": tid})
    assert response.status_code == 200, response.text
    assert rt.pipeline.locks["subject"].track_id == tid
    assert rt.pipeline.autonomy.mode == "MANUAL" and not rt.cfg.autonomy.allow_real_flight
    assert commands == []


def test_incident_switch_and_reset_archive_history_and_invalidate_search(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    tid = rt.pipeline.raw_tracks[0].track_id
    rt.pipeline.lock_track("subject", tid)
    rt.incidents.targets.update()
    old = c.get(f"/api/incidents/{iid}/target").json()["subject"]
    rt.pipeline.reset_session("replacement-session")
    rt.incidents.targets.update()
    assert c.get(f"/api/incidents/{iid}/target").json()["subject"] is None
    saved = rt.incidents.store.list_targets(iid)
    assert saved[0]["logical_subject_id"] == old["logical_subject_id"]
    assert saved[0]["state"] == "LOST"
    restored = rebuild(rt.incidents.store.events(iid))
    assert restored.list_targets(iid)[0]["logical_subject_id"] == old["logical_subject_id"]
    new = c.post("/api/incidents", json={"title": "New incident"}).json()["id"]
    assert c.get(f"/api/incidents/{new}/target").json()["history"] == []
    assert c.post(f"/api/incidents/{iid}/target/search", json={"description": "red shirt"}).status_code == 409


def test_tracker_epoch_change_cannot_apply_an_old_continuous_request(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    c.post(f"/api/incidents/{iid}/target/search", json={"description": "red shirt"})
    rt.pipeline.reset_session()
    for n in range(7, 15): frame(n); rt.incidents.targets.update()
    assert rt.pipeline.locks["subject"].state == "UNLOCKED"
    assert rt.incidents.person_mark.requests[iid]["status"] == "released"


def test_selected_subject_survives_gap_expired_track_and_unique_simulated_reacquisition(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    p = rt.pipeline
    original_track = p.raw_tracks[0].track_id
    p.lock_track("subject", original_track)
    rt.incidents.targets.update()
    original = rt.incidents.targets.subject.logical_id
    boxes = p.detector.boxes
    p.detector.boxes = [boxes[1]]
    for n in range(7, 61):
        frame(n)
        rt.incidents.targets.update()
    lost = c.get(f"/api/incidents/{iid}/target").json()["subject"]
    assert lost["logical_subject_id"] == original and lost["measured_position"] is None
    p.detector.boxes = boxes
    for n in range(61, 76):
        frame(n)
        rt.incidents.targets.update()
    result = c.get(f"/api/incidents/{iid}/target").json()["subject"]
    assert result["logical_subject_id"] == original
    assert result["last_track_id"] != original_track
    assert result["state"] == "VISIBLE" and result["binding_verified"]
    assert rt.pipeline.autonomy.mode == "MANUAL"


def test_camera_motion_compensation_keeps_logical_subject_and_ground_position(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    p = rt.pipeline
    p.lock_track("subject", p.raw_tracks[0].track_id)
    rt.incidents.targets.update()
    original = rt.incidents.targets.subject.snapshot()
    p.cmc.estimate = lambda *_: SimpleNamespace(ok=True, H=np.array([[1., 0, 10], [0, 1., 0], [0, 0, 1.]]), inliers=40, shift_px=10.)
    p.detector.boxes = [(x0+10, y0, x1+10, y1) for x0, y0, x1, y1 in p.detector.boxes]
    frame(7)
    rt.incidents.targets.update()
    current = rt.incidents.targets.subject.snapshot()
    assert current["logical_subject_id"] == original["logical_subject_id"]
    assert abs(current["measured_position"]["x"] - original["measured_position"]["x"]) < .1


def test_delayed_capture_does_not_become_fresh_target_evidence(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch)
    p = rt.pipeline
    p.lock_track("subject", p.raw_tracks[0].track_id)
    rt.incidents.targets.update()
    p.counters["drone"].last_capture_ms -= 2000
    rt.incidents.targets.update()
    assert rt.incidents.targets.subject.snapshot()["measured_position"] is None


def test_live_unverified_rebinding_is_never_automatically_confirmed(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch, "LIVE")
    p = rt.pipeline
    tid = p.raw_tracks[0].track_id
    p.lock_track("subject", tid)
    rt.incidents.targets.update()
    # Exercise the coordinator's automatic-continuity entry point with a current,
    # clean measurement. LIVE must never authorize it, regardless of simulated
    # geometry or appearance being otherwise favorable in this fixture.
    lock = p.locks["subject"]
    lock.verified = False
    for n in range(7, 13):
        frame(n)
        lock.verified = False
        rt.incidents.targets.update()
        assert lock.verified is False
    assert rt.incidents.targets.subject.snapshot()["state"] == "UNCERTAIN"
    assert not any(e.kind == "target_continuity_accepted" for e in rt.incidents.store.events(iid))
    assert p.autonomy.mode == "MANUAL" and not rt.cfg.autonomy.allow_real_flight
