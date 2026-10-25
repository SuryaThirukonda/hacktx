"""Website setup is explicit, cannot turn a legacy stream into control, and retains RC/ground guards."""
import asyncio

from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import load_config
from overwatch.schemas import BridgeCommand, DroneTelemetry
from overwatch.sources.live import LiveBridge


def test_website_setup_requires_capability_fresh_telemetry_and_props_confirmation(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = "test-web"
    cfg.autonomy.allow_real_flight = False
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline; b = LiveBridge(rt)
    p.autonomy.bridge = b
    client = TestClient(app)
    path = "/api/autonomy/bridge/setup"
    assert client.post(path, json={"action":"operator_rearm"}).status_code == 401
    client.headers["Authorization"] = "Bearer test-web"
    assert client.post(path, json={"action":"operator_rearm"}).status_code == 409
    b.out = asyncio.Queue(); b.control_capable = True; b.web_control_supported = True
    b.control_token = "generation-1"; b.last_rx_ms = rt.clock.now_ms()
    assert client.post(path, json={"action":"sim_start", "props_removed":True}).status_code == 409
    p.telemetry = DroneTelemetry(capture_ts_ms=0, flying=False)
    p.counters["telemetry"].last_rx_ms = rt.clock.now_ms()
    messages = []; b.send = messages.append
    assert client.post(path, json={"action":"sim_start"}).status_code == 409
    p.telemetry.flying = True
    assert client.post(path, json={"action":"sim_start", "props_removed":True}).status_code == 409
    p.telemetry.flying = False
    assert client.post(path, json={"action":"sim_start", "props_removed":True}).status_code == 200
    assert messages[-1].type == "sim_start"
    assert client.post(path, json={"action":"operator_rearm"}).status_code == 409
    b.sim_active = True
    assert client.post(path, json={"action":"operator_rearm"}).status_code == 200
    assert messages[-1].payload == {"allow_real_flight":False,"allow_translation":False}
    assert p.autonomy.mode == "MANUAL"
    assert all(m.type != "vstick" for m in messages)
    for kind in ("operator_rearm", "sim_start", "sim_stop"):
        assert b.permits(BridgeCommand(type=kind))  # SDK gets to perform neutral RC checks while inhibited
        assert not b.permits(BridgeCommand(type=kind, payload={"control_token":"old"}), queued=True)
        b.legacy = True
        assert not b.permits(BridgeCommand(type=kind))
        b.legacy = False
    b.web_control_supported = False
    assert not b.permits(BridgeCommand(type="operator_rearm"))


def test_readiness_reports_marking_is_not_movement(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    app = create_app(cfg); response = TestClient(app).get("/api/autonomy/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["autonomy"]["mode"] == "MANUAL" and not body["bridge_capable"]
    assert any("Start Stream" in message for message in body["blockers"])
    assert any("Designation is not movement" in message for message in body["blockers"])


def test_explicit_website_camera_enable_is_bounded_and_never_starts_movement(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    cfg.autonomy.motion_profile = "camera_only"; cfg.autonomy.allow_real_flight = False
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline; b = LiveBridge(rt)
    p.autonomy.bridge = b
    client = TestClient(app); path = "/api/autonomy/bridge/setup"
    body = {"action":"enable_camera_control"}
    # No live link, then stale/missing telemetry, then missing video each block explicit enable.
    assert client.post(path, json=body).status_code == 409
    b.out = asyncio.Queue(); b.control_capable = b.web_control_supported = True
    b.control_token = "generation-1"; b.last_rx_ms = rt.clock.now_ms()
    assert client.post(path, json=body).status_code == 409
    p.telemetry = DroneTelemetry(capture_ts_ms=0, alt_rel_m=1.5, yaw_deg=0, battery_pct=80,
                                 gimbal_pitch_deg=-20, flying=True)
    p.counters["telemetry"].last_rx_ms = rt.clock.now_ms()
    assert client.post(path, json=body).status_code == 409
    p.counters["drone"].last_rx_ms = rt.clock.now_ms()
    p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
    messages = []; b.send = messages.append
    # Full-motion cannot be opted in by the simple camera-control button.
    cfg.autonomy.motion_profile = "full"
    assert client.post(path, json=body).status_code == 409
    cfg.autonomy.motion_profile = "camera_only"
    p.telemetry.battery_pct = 5
    assert client.post(path, json=body).status_code == 409
    p.telemetry.battery_pct = 80
    assert not cfg.autonomy.allow_real_flight
    assert client.post(path, json=body).status_code == 200
    assert cfg.autonomy.allow_real_flight
    assert messages[-1].type == "operator_rearm"
    assert messages[-1].payload == {"allow_real_flight":True, "allow_translation":False}
    assert p.autonomy.mode == "MANUAL" and b.control_inhibited
    assert all(m.type not in ("vstick", "gimbal", "rtl") for m in messages)
    assert client.post("/api/autonomy/mode", json={"mode":"FOLLOW"}).status_code == 409
    b.control_inhibited = False
    # A mode switch alone cannot start follow with no current verified subject.
    assert client.post("/api/autonomy/mode", json={"mode":"FOLLOW"}).status_code == 409
    # Stop remains available after connection/telemetry loss.
    b.out = None; p.counters["telemetry"].last_rx_ms = None
    assert client.post(path, json={"action":"disable_control"}).status_code == 200
    assert not cfg.autonomy.allow_real_flight and p.autonomy.mode == "MANUAL"
    assert client.post(path, json={"action":"operator_rearm"}).status_code == 409


def test_dji_rejection_remains_visible_across_status_heartbeats(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"
    rt = create_app(cfg).state.rt; b = LiveBridge(rt); rt.pipeline.bridge = b
    b.on_text({"type":"audit", "text":"virtual stick enable failed: SDK rejects command"})
    for _ in range(15):
        b.on_text({"type":"status", "text":"vstick=DISABLED"})
    assert rt.pipeline.extras(rt.clock.now_ms())["bridge_control"]["error"] == "virtual stick enable failed: SDK rejects command"
    b.on_text({"type":"audit", "text":"virtual stick ENABLED (Mac mode command)"})
    assert b.control_error is None
    b.control_error = "old"; b.reset_connection()
    assert b.control_error is None


def test_movement_enable_requires_sdk_support_and_explicitly_arms_translation(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = "movement-test"
    cfg.autonomy.motion_profile = "camera_only"; cfg.autonomy.allow_real_flight = False
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline; b = LiveBridge(rt); p.autonomy.bridge = b
    client = TestClient(app); path = "/api/autonomy/bridge/setup"; body = {"action":"enable_visual_follow"}
    assert client.post(path, json=body).status_code == 401
    client.headers["Authorization"] = "Bearer movement-test"
    b.out = asyncio.Queue(); b.control_capable = b.web_control_supported = True
    b.last_rx_ms = rt.clock.now_ms(); b.control_token = "generation-1"
    p.telemetry = DroneTelemetry(capture_ts_ms=0, alt_rel_m=2, yaw_deg=0, battery_pct=80, gimbal_pitch_deg=-20, flying=True)
    for source in ("drone", "telemetry"): p.counters[source].last_rx_ms = rt.clock.now_ms()
    p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
    sent = []; b.send = sent.append
    assert client.post(path, json=body).status_code == 409
    assert cfg.autonomy.motion_profile == "camera_only" and not cfg.autonomy.allow_real_flight
    b.translation_supported = True
    assert client.post(path, json=body).status_code == 200
    assert cfg.autonomy.motion_profile == "visual_follow" and cfg.autonomy.allow_real_flight
    assert sent[-1].type == "operator_rearm" and sent[-1].payload["allow_translation"] is True
    assert p.autonomy.mode == "MANUAL" and all(m.type != "vstick" for m in sent)
    b.control_inhibited = False
    assert "permission" in p._real_flight_problem(rt.clock.now_ms())
    b.translation_armed = True
    assert p._real_flight_problem(rt.clock.now_ms()) is None
    # Going back to rotation-only removes the translation opt-in and stops the previous session.
    assert client.post(path, json={"action":"enable_camera_control"}).status_code == 200
    assert cfg.autonomy.motion_profile == "camera_only" and not sent[-1].payload["allow_translation"]


def test_startup_and_reconnect_drop_physical_permission(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.autonomy.allow_real_flight = True
    rt = create_app(cfg).state.rt
    assert not cfg.autonomy.allow_real_flight
    b = LiveBridge(rt); rt.pipeline.autonomy.bridge = b
    cfg.autonomy.allow_real_flight = True; rt.pipeline.autonomy.mode = "FOLLOW"
    b.reset_connection()
    assert not cfg.autonomy.allow_real_flight and rt.pipeline.autonomy.mode == "MANUAL"
    assert b.control_inhibited and b.control_token is None


def test_wire_sequence_spans_every_command_and_rearm_does_not_echo_disable(monkeypatch):
    import json
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"
    rt = create_app(cfg).state.rt; b = LiveBridge(rt); rt.pipeline.autonomy.bridge = b
    class Loop:
        def call_soon_threadsafe(self, function, *args): function(*args)
    rt.loop = Loop(); b.out = asyncio.Queue(maxsize=20)
    b.control_capable = b.web_control_supported = True; b.control_inhibited = False; b.control_token = "one"
    kinds = ["mode", "vstick", "gimbal", "rtl", "estop", "operator_rearm", "sim_start", "sim_stop"]
    for kind in kinds:
        b.send(BridgeCommand(type=kind, seq=0, ts_ms=100, payload={"seq":0, "vstick":True}))
    messages = [json.loads(b.out.get_nowait()) for _ in kinds]
    assert [m["seq"] for m in messages] == list(range(1, len(kinds)+1))
    assert all(m["payload"]["ttl_ms"] == 300 for m in messages)
    assert messages[1]["payload"]["seq"] == messages[1]["seq"]
    b.on_text({"type":"status", "control_inhibited":False, "control_token":"two"})
    assert b.out.empty(), "native re-arm status must not trigger a disabling echo"
    assert rt.pipeline.autonomy.mode == "MANUAL"


def test_setup_waits_for_disable_and_concurrent_stop_cancels_rearm(monkeypatch):
    import threading
    import time
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    cfg.autonomy.motion_profile = "camera_only"
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline; b = LiveBridge(rt); p.autonomy.bridge = b
    b.out = asyncio.Queue(); b.control_capable = b.web_control_supported = True
    b.control_token = "one"; b.last_rx_ms = rt.clock.now_ms(); b.control_phase = "ENABLED"
    p.telemetry = DroneTelemetry(capture_ts_ms=0, alt_rel_m=2, yaw_deg=0, battery_pct=80,
                                 gimbal_pitch_deg=-20, flying=True)
    for source in ("drone", "telemetry"): p.counters[source].last_rx_ms = rt.clock.now_ms()
    p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
    sent = []; b.send = sent.append
    path = "/api/autonomy/bridge/setup"
    with TestClient(app) as c:
        p.autonomy.bridge = b
        # This test models SDK completion without connecting hardware.
        result = []
        t = threading.Thread(target=lambda: result.append(c.post(path, json={"action":"enable_camera_control"})))
        t.start()
        deadline = time.monotonic()+1
        while not cfg.autonomy.allow_real_flight:
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert not any(m.type == "operator_rearm" for m in sent)
        assert c.post(path, json={"action":"disable_control"}).status_code == 200
        b.control_phase = "DISABLED"
        t.join(3)
        assert result and result[0].status_code == 409
        assert not cfg.autonomy.allow_real_flight and not any(m.type == "operator_rearm" for m in sent)


def test_full_profile_enable_preserves_site_gates_and_separate_start(monkeypatch):
    from types import SimpleNamespace
    from overwatch.schemas import DroneState
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.secrets.overwatch_token = None
    cfg.autonomy.motion_profile = "full"; cfg.autonomy.geofence = [(-10,-10),(10,-10),(10,10),(-10,10)]
    app = create_app(cfg); rt = app.state.rt; p = rt.pipeline; b = LiveBridge(rt); p.autonomy.bridge = b
    b.out = asyncio.Queue(); b.control_capable = b.web_control_supported = b.translation_supported = True
    b.control_token = "one"; b.last_rx_ms = rt.clock.now_ms()
    p.telemetry = DroneTelemetry(capture_ts_ms=0, alt_rel_m=20, yaw_deg=0, battery_pct=80,
                                 gimbal_pitch_deg=-45, flying=True, gps_level=5, sats=10)
    for source in ("drone", "telemetry"): p.counters[source].last_rx_ms = rt.clock.now_ms()
    p.counters["drone"].provenance = "LIVE"; p.last_drone_source_id = "dji-bridge"
    # Modeled state only; no real bridge socket, SDK or aircraft command is involved.
    p.drone_state = lambda now: DroneState(position_xy_m=(0,0), alt_m=20, yaw_deg=0, battery_pct=80,
                                          gimbal_pitch_deg=-45, flying=True)
    c = TestClient(app); path = "/api/autonomy/bridge/setup"
    assert c.post(path, json={"action":"enable_full_control"}).status_code == 409  # no calibration
    p.calib = SimpleNamespace(valid=True)
    p.telemetry.gps_level = 1
    assert c.post(path, json={"action":"enable_full_control"}).status_code == 409
    p.telemetry.gps_level = 5
    sent = []; b.send = sent.append
    r = c.post(path, json={"action":"enable_full_control"})
    assert r.status_code == 200, r.text
    assert cfg.autonomy.allow_real_flight and cfg.autonomy.motion_profile == "full"
    assert sent[-1].type == "operator_rearm" and sent[-1].payload["allow_translation"]
    assert p.autonomy.mode == "MANUAL" and b.control_inhibited
    assert cfg.autonomy.follow_max_speed_mps <= 1 and cfg.autonomy.max_vertical_mps <= .5
    assert all(m.type not in ("vstick", "rtl", "gimbal") for m in sent)


def test_recorded_or_other_camera_cannot_authorize_physical_follow(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"
    rt = create_app(cfg).state.rt; p = rt.pipeline; b = LiveBridge(rt); p.autonomy.bridge = b
    b.control_capable = True; b.control_inhibited = False; b.control_token = "one"
    cfg.autonomy.allow_real_flight = True
    for source in ("drone", "telemetry"): p.counters[source].last_rx_ms = rt.clock.now_ms()
    for provenance, source_id in [("RECORDED", "dji-bridge"), ("SYNTHETIC", "dji-bridge"), ("LIVE", "url")]:
        p.counters["drone"].provenance = provenance; p.last_drone_source_id = source_id
        assert "LIVE video from the DJI bridge" in p._real_flight_problem(rt.clock.now_ms())
    p.flight_confirmed_track_id = "old-camera-track"; p.autonomy.mode = "FOLLOW"
    b.reset_connection()
    assert p.flight_confirmed_track_id is None and p.autonomy.mode == "MANUAL"
    assert not cfg.autonomy.allow_real_flight


def test_source_status_is_read_only_and_source_switch_disarms(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"
    rt = create_app(cfg).state.rt; p = rt.pipeline
    cfg.autonomy.allow_real_flight = True; p.autonomy.mode = "FOLLOW"
    p.flight_confirmed_track_id = "current-subject"
    rt.video.status("dji_mini2")
    assert cfg.autonomy.allow_real_flight and p.autonomy.mode == "FOLLOW"
    assert p.flight_confirmed_track_id == "current-subject"
    from overwatch.sources.video import SourceError
    import pytest
    with pytest.raises(SourceError, match="file not found"):
        rt.video.select("file", "/definitely/missing/clip.mp4")
    assert cfg.autonomy.allow_real_flight and p.autonomy.mode == "FOLLOW"
    rt.video.select("dji_mini2")
    assert not cfg.autonomy.allow_real_flight and p.autonomy.mode == "MANUAL"
    assert p.flight_confirmed_track_id is None
