"""City remains isolated; scan recovery is bounded and access checks precede GPU work."""
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from overwatch.api.app import create_app
from overwatch.config import Config
from test12.server import Manager, mount
from test12.sim import Run


def test_city_scan_inspects_cover_then_resumes_same_belief():
    run = Run.__new__(Run)
    run.phase = "SCAN"
    run.scan = dict(turned=360., t0=0., centre=np.array([10., 20.]), turn=1.)
    run.nav_xy = lambda: np.array([10., 40.])
    run.planner = SimpleNamespace(pick="previous")
    run.pick = "previous"
    run.last_seen = dict(xy=(10., 20.), t=0.)
    run.belief = object()
    run.survey = object()
    before = run.belief, run.survey, run.last_seen
    events = []
    run.event = lambda kind, text: events.append(kind)
    run._advance_loss_strategy(7.)
    assert run.phase == "ORBIT"
    run._advance_loss_strategy(16.9)
    assert run.phase == "ORBIT"
    run._advance_loss_strategy(17.)
    assert run.phase == "SEARCH" and run.pick is None
    assert before == (run.belief, run.survey, run.last_seen)
    assert events == ["orbit", "search"]


def test_city_stop_timeout_never_resumes_main():
    manager = Manager()
    manager.started = True
    manager.conn = SimpleNamespace(send=lambda msg: None)
    manager.stop_ack = SimpleNamespace(clear=lambda: None, wait=lambda timeout: False)
    manager._resume_main = True
    with pytest.raises(RuntimeError, match="remains paused"):
        manager.stop()
    assert manager._resume_main


def test_city_auth_and_workspace_catalogue_without_gpu_start(monkeypatch):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = Config()
    cfg.secrets.overwatch_token = "local-test"
    app = create_app(cfg)
    mount(app)
    client = TestClient(app)
    for path in ("/api/test12/state", "/api/test12/voice", "/api/tts?text=test", "/api/rescue/voice/1.mp3"):
        assert client.get(path).status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/test12/sim3d") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
    library = client.get("/api/usecases").json()["items"]
    ids = {s["id"] for world in library for s in world["scenarios"]}
    assert {"T12", "CT1", "CT2", "SAR1", "FR3", "FF3", "SR5", "PP8", "VC1", "VC2"} <= ids
