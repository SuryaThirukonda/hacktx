"""Actual FastAPI transport -> decode -> detector/tracker, plus pilot priority during slow inference."""
import threading
import time
from fractions import Fraction
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from overwatch.api.app import create_app
from overwatch.config import load_config
from overwatch.decisions.tasks import d1_reacquire
from overwatch.schemas import BridgeCommand, DecisionRecord, DroneState
from overwatch.sources.live import pack_binary_frame
from overwatch.vision.detectors import Detection


class PersonDetector:
    model_id = "test-person-detector"
    valid_on = "any"

    def detect(self, image):
        return [Detection((20., 10., 50., 85.), 0.95)]


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition did not become true"
        time.sleep(0.01)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config()
    cfg.session.mode = "LIVE"
    cfg.sources.drone.url = None
    cfg.sources.drone.adapter = "bridge"
    cfg.secrets.openai_api_key = None
    cfg.secrets.typesafe_api_key = None
    cfg.secrets.overwatch_token = "test-token"
    cfg.decisions.primary = "rules"
    cfg.decisions.shadow = []
    app = create_app(cfg)
    p = app.state.rt.pipeline
    p.detector = PersonDetector()
    p._detector_prov = ("LIVE", cfg.perception.detector)
    with TestClient(app) as client:
        yield client, app.state.rt


def jpeg():
    return cv2.imencode(".jpg", np.full((96, 128, 3), 100, np.uint8))[1].tobytes()


@pytest.mark.parametrize("path,wrapped", [("/ws", False), ("/ws/bridge", True)])
def test_video_enters_detector_and_tracker(live, path, wrapped):
    client, rt = live
    with client.websocket_connect(path + "?token=test-token") as ws:
        for i in range(4):
            payload = pack_binary_frame(1, time.monotonic() * 1000, jpeg()) if wrapped else jpeg()
            ws.send_bytes(payload)
            wait_until(lambda i=i: rt.pipeline.counters["drone"].processed >= i + 1)
        assert rt.pipeline.latest_drone.image.shape == (96, 128, 3)
        assert rt.pipeline.raw_tracks
        assert rt.pipeline.detector.model_id == "test-person-detector"
        assert not rt.live.bridge.control_capable


def test_http_test_frame_and_auth(live):
    client, rt = live
    assert client.post("/frame", content=jpeg()).status_code == 401
    assert client.post("/frame?token=test-token", content=b"invalid").status_code == 422
    assert client.post("/frame?token=test-token", content=b"").status_code == 422
    result = client.post("/frame?token=test-token", content=jpeg())
    assert result.status_code == 200 and result.json()["control"] == "video_only"
    assert rt.pipeline.latest_drone is not None


@pytest.mark.parametrize("path,wrapped", [("/ws", False), ("/ws/bridge", True)])
def test_http_test_frame_while_streaming_is_a_non_disruptive_diagnostic(live, path, wrapped):
    client, rt = live
    with client.websocket_connect(path+"?token=test-token") as ws:
        ws.send_bytes(pack_binary_frame(1, time.monotonic()*1000, jpeg()) if wrapped else jpeg())
        wait_until(lambda: rt.pipeline.counters["drone"].processed == 1)
        before = rt.pipeline.latest_drone
        out = rt.live.bridge.out
        response = client.post("/frame?token=test-token", content=jpeg())
        assert response.status_code == 200, response.text
        assert response.json()["diagnostic_only"] and response.json()["width"] == 128
        assert rt.pipeline.latest_drone is before and rt.live.bridge.out is out
        assert rt.pipeline.counters["drone"].processed == 1
        assert client.post("/frame?token=test-token", content=b"bad JPEG").status_code == 422
        ws.send_bytes(pack_binary_frame(1, time.monotonic()*1000, jpeg()) if wrapped else jpeg())
        wait_until(lambda: rt.pipeline.counters["drone"].processed == 2)


def test_override_is_processed_while_detector_is_blocked(live):
    client, rt = live
    started, release = threading.Event(), threading.Event()

    class SlowDetector(PersonDetector):
        def detect(self, image):
            started.set()
            release.wait(3)
            return super().detect(image)

    rt.pipeline.detector = SlowDetector()
    with client.websocket_connect("/ws/bridge?token=test-token") as ws:
        ws.send_json({"type": "hello", "pilot_override_protocol": 1})
        ws.send_json({"type": "status", "control_inhibited": False, "control_token": "rearm-1"})
        wait_until(lambda: not rt.live.bridge.control_inhibited)
        rt.pipeline.autonomy.set_mode("FOLLOW", rt.clock.now_ms())
        ws.send_bytes(pack_binary_frame(1, time.monotonic() * 1000, jpeg()))
        assert started.wait(2)
        try:
            ws.send_json({"type": "audit", "event": "pilot_override", "text": "physical stick moved"})
            wait_until(lambda: rt.pipeline.autonomy.mode == "MANUAL", timeout=0.8)
            assert rt.live.bridge.control_inhibited
            assert rt.pipeline.autonomy.route == []
        finally:
            release.set()


def test_old_jev_answer_cannot_reapply_after_manual_then_follow(live):
    _, rt = live
    p = rt.pipeline
    p.autonomy.set_mode("FOLLOW", rt.clock.now_ms())
    job = d1_reacquire({"time_since_loss_s": 2.0})
    job.context["control_epoch"] = p.autonomy.control_epoch
    rec = DecisionRecord(decision_id="old-jev", task=job.task, engine="typesafe", status="success",
                         request_ts_ms=rt.clock.now_ms(), selected="orbit_last_seen", acted=True)
    p.autonomy.set_mode("MANUAL", rt.clock.now_ms(), "physical controller")
    p.autonomy.set_mode("FOLLOW", rt.clock.now_ms(), "operator re-enabled")
    p.apply_decision(job, [rec])
    assert p.autonomy.primitive is None
    assert not rec.acted and rec.facts.get("_dropped")
    job.context["control_epoch"] = p.autonomy.control_epoch
    current = rec.model_copy(update={"acted": True, "facts": {}})
    p.apply_decision(job, [current])
    assert p.autonomy.primitive == "orbit_last_seen"
    assert p.autonomy.primitive_source == "decision:typesafe"


def test_second_bridge_cannot_replace_active_source(live):
    client, _ = live
    with client.websocket_connect("/ws?token=test-token"):
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws/bridge?token=test-token"):
                pass
        assert error.value.code == 4409


def test_legacy_never_receives_flight_commands_even_if_it_claims_capability(live):
    client, rt = live
    with client.websocket_connect("/ws?token=test-token") as ws:
        ws.send_json({"type": "hello", "pilot_override_protocol": 1})
        ws.send_json({"type": "status", "control_inhibited": False, "control_token": "forged"})
        ws.send_bytes(jpeg())
        wait_until(lambda: rt.pipeline.latest_drone is not None)
        assert rt.live.bridge.legacy
        assert not rt.live.bridge.permits(BridgeCommand(type="mode", payload={"vstick": True}))
        assert not rt.live.bridge.permits(BridgeCommand(type="vstick", payload={"forward_mps": 2}))


def test_rearm_invalidates_buffered_commands(live):
    client, rt = live
    with client.websocket_connect("/ws/bridge?token=test-token") as ws:
        ws.send_json({"type": "hello", "pilot_override_protocol": 1})
        ws.send_json({"type": "status", "control_inhibited": False, "control_token": "generation-1"})
        wait_until(lambda: rt.live.bridge.control_token == "generation-1")
        cmd = BridgeCommand(type="vstick", payload={"control_token": "generation-1", "forward_mps": 2})
        assert rt.live.bridge.permits(cmd, queued=True)
        ws.send_json({"type": "audit", "event": "pilot_override"})
        wait_until(lambda: rt.live.bridge.control_inhibited)
        assert not rt.live.bridge.permits(cmd, queued=True)
        ws.send_json({"type": "status", "control_inhibited": False, "control_token": "generation-2"})
        wait_until(lambda: rt.live.bridge.control_token == "generation-2")
        assert not rt.live.bridge.permits(cmd, queued=True)
        assert rt.live.bridge.permits(BridgeCommand(type="estop"), queued=True)


def test_h264_stream_decodes_into_the_same_pipeline(live):
    av = pytest.importorskip("av")
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height = 128, 96
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 30)
    encoder.options = {"tune": "zerolatency", "g": "2"}
    chunks = []
    for i in range(6):
        frame = av.VideoFrame.from_ndarray(np.full((96, 128, 3), 100, np.uint8), format="bgr24")
        frame.pts = i
        chunks.extend(bytes(p) for p in encoder.encode(frame))
    chunks.extend(bytes(p) for p in encoder.encode(None))
    client, rt = live
    with client.websocket_connect("/ws/bridge?token=test-token") as ws:
        for data in chunks:
            ws.send_bytes(pack_binary_frame(2, time.monotonic() * 1000, data))
        wait_until(lambda: rt.pipeline.latest_drone is not None)
        assert rt.pipeline.latest_drone.image.shape == (96, 128, 3)


def test_real_flight_requires_readiness_and_stale_telemetry_returns_manual(live):
    _, rt = live
    p = rt.pipeline
    with p.lock:
        p.cfg.autonomy.allow_real_flight = True
        with pytest.raises(ValueError, match="SDK stream"):
            p.set_autonomy_mode("FOLLOW")
        rt.live.bridge.control_capable = True
        rt.live.bridge.control_inhibited = False
        rt.live.bridge.translation_supported = True
        rt.live.bridge.translation_armed = True
        with pytest.raises(ValueError, match="drone stale or missing"):
            p.set_autonomy_mode("FOLLOW")
        now = rt.clock.now_ms()
        for src in ("drone", "telemetry"):
            p.counters[src].last_rx_ms = now
        p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
        original_calib, original_drone_state = p.calib, p.drone_state
        try:
            p.drone_state = lambda _: DroneState(position_xy_m=(0, 0), alt_m=18, yaw_deg=0, battery_pct=80, flying=True)
            with pytest.raises(ValueError, match="ground calibration"):
                p.set_autonomy_mode("FOLLOW")
            p.calib = SimpleNamespace(valid=True)
            from overwatch.config import GeoRef
            from overwatch.schemas import DroneTelemetry
            p.cfg.world.geo_reference = GeoRef(lat=30, lon=-97)
            p.telemetry = DroneTelemetry(capture_ts_ms=now, flying=True, gps_level=5, sats=12)
            assert p.set_autonomy_mode("FOLLOW") == "FOLLOW"
            p.counters["telemetry"].last_rx_ms = now - 2000
            p.autonomy_tick(now)
            assert p.autonomy.mode == "MANUAL"
            assert "telemetry stale" in p.autonomy.audit[-1]["text"]
        finally:
            p.calib, p.drone_state = original_calib, original_drone_state


def test_camera_only_uses_image_geometry_without_requiring_gps_or_ground_calibration(live):
    _, rt = live
    p = rt.pipeline
    with p.lock:
        p.cfg.autonomy.allow_real_flight = True
        p.cfg.autonomy.motion_profile = "camera_only"
        rt.live.bridge.control_capable = True
        rt.live.bridge.control_inhibited = False
        rt.live.bridge.motion_profile = "camera_only"
        now = rt.clock.now_ms()
        for src in ("drone", "telemetry"):
            p.counters[src].last_rx_ms = now
        p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
        original = p.drone_state
        try:
            state = DroneState(alt_m=3, yaw_deg=0, battery_pct=80, gimbal_pitch_deg=-30, flying=True)
            p.drone_state = lambda _: state
            assert p.set_autonomy_mode("FOLLOW") == "FOLLOW"
            p.autonomy_tick(now)
            assert p.autonomy.last_cmd.up_mps == 0
            p.set_autonomy_mode("MANUAL")
            state.flying = False
            with pytest.raises(ValueError, match="airborne aircraft"):
                p.set_autonomy_mode("FOLLOW")
        finally:
            p.drone_state = original


def test_box_selection_and_follow_designation_are_separate(live):
    client, rt = live
    token = {"token": "test-token"}
    incident = client.post("/api/incidents", params=token,
                           json={"title": "Camera follow test", "mission_mode": "follow"}).json()
    with client.websocket_connect("/ws?token=test-token") as ws:
        for i in range(4):
            ws.send_bytes(jpeg())
            wait_until(lambda i=i: rt.pipeline.counters["drone"].processed >= i + 1)
        tid = rt.pipeline.raw_tracks[0].track_id
        assert client.post(f"/api/tracks/{tid}/select", params=token).status_code == 200
        assert rt.pipeline.locks["subject"].state == "UNLOCKED"
        result = client.post(f"/api/incidents/{incident['id']}/follow/designate", params=token,
                             json={"track_id": tid})
        assert result.status_code == 200, result.text
        assert rt.pipeline.locks["subject"].track_id == tid
        assert rt.pipeline.autonomy.mode == "MANUAL"  # designation itself never enables aircraft control
