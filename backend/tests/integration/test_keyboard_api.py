"""Authenticated keyboard lease API; no sockets, hardware or external model calls."""
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.config import load_config


class Bridge:
    is_sim = True
    control_capable = True
    control_inhibited = False
    def connected(self, now): return True
    def send(self, command): pass


def test_keyboard_api_auth_validation_and_pilot_latch():
    cfg = load_config()
    cfg.autonomy.motion_profile = "camera_only"
    cfg.secrets.overwatch_token = "keyboard-test"
    app = create_app(cfg)
    bridge = Bridge()
    app.state.rt.pipeline.autonomy.bridge = bridge
    client = TestClient(app)  # no background runtime: exercise API lease deterministically
    path = "/api/autonomy/keyboard/"
    assert client.post(path + "start").status_code == 401
    client.headers["Authorization"] = "Bearer keyboard-test"
    started = client.post(path + "start")
    assert started.status_code == 200, started.text
    session = started.json()["session"]
    assert client.post(path + "start").status_code == 409
    body = {"session": session, "seq": 1, "yaw": 1, "tilt": -1}
    assert client.post(path + "input", json={**body, "yaw": 2}).status_code == 422
    assert client.post(path + "input", json=body).status_code == 200
    bridge.control_inhibited = True
    assert client.post(path + "input", json={**body, "seq": 2}).status_code == 409
    assert client.post(path + "stop", json={"session": session, "seq": 3}).status_code == 200
    assert app.state.rt.pipeline.autonomy.mode == "MANUAL"
    assert bridge.control_inhibited  # the web API cannot re-arm RC takeover


def test_manual_movement_axes_are_validated_and_camera_only_rejects_translation():
    cfg = load_config(); cfg.session.mode = "SIMULATED"; cfg.secrets.overwatch_token = None
    cfg.autonomy.motion_profile = "visual_follow"
    app = create_app(cfg); p = app.state.rt.pipeline; p.autonomy.bridge = Bridge()
    client = TestClient(app); path = "/api/autonomy/keyboard/"
    session = client.post(path+"start").json()["session"]
    axes = {"session":session, "seq":1, "yaw":0, "tilt":-1, "forward":1, "right":-1}
    assert client.post(path+"input", json={**axes,"forward":1.1}).status_code == 422
    assert client.post(path+"input", json=axes).status_code == 200
    assert p.autonomy.keyboard_axes == (0,-1,1,-1)
    assert client.post(path+"stop", json={"session":session,"seq":2}).status_code == 200
    cfg.autonomy.motion_profile = "camera_only"
    axes["session"] = client.post(path+"start").json()["session"]
    assert client.post(path+"input", json=axes).status_code == 409
