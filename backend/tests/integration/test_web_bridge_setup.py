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
