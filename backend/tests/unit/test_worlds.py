"""Phase 14: the four worlds are wired as scenarios with limits; hazards and thermal are labelled; the API serves them."""

import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app
from overwatch.sim.scenarios import all_scenarios

WORLD_PREFIXES = {"PP": "pursuit", "PJ": "pursuit", "CT": "city", "SR": "sar", "FR": "flood", "FF": "fire"}


def test_every_world_scenario_has_limits_and_stays_in_its_world():
    sc = all_scenarios()
    ids = [k for k in sc if k[:2] in WORLD_PREFIXES]
    assert {k[:2] for k in ids} == set(WORLD_PREFIXES)
    for k in ids:
        s = sc[k]
        assert s.limits, f"{k} has no limits"
        assert f"world:{WORLD_PREFIXES[k[:2]]}" in s.tags
        w = s.world()
        x0, x1, y0, y1 = w.bounds
        for x, y, alt, _ in s.pilot_path:
            assert x0 <= x <= x1 and y0 <= y <= y1 and 10 <= alt <= 60, (k, x, y, alt)
        d = s.drone
        assert x0 <= d.x <= x1 and y0 <= d.y <= y1 and 0 <= d.alt <= 60, (k, d.x, d.y, d.alt)
        if d.alt < 1.0:  # on the ground: it must be on the world's launchpad and the scenario must dispatch it
            assert w.launchpad and abs(d.x - w.launchpad[0]) < 1 and abs(d.y - w.launchpad[1]) < 1, k
            assert any(a.action == "dispatch" for a in s.actions), f"{k} starts on the ground but never launches"
        for a in s.actors:
            assert x0 <= a.start_xy[0] <= x1 and y0 <= a.start_xy[1] <= y1, (k, a.actor_id)
    assert {"demo", "DEMO-1", "S05", "S16", "OW7", "PP0", "PP8", "CT1", "CT2"} <= set(sc)


def test_hazard_layers_are_labelled_screening_models():
    sc = all_scenarios()
    for k in ("FR1", "FF1"):
        hz = sc[k].hazards
        assert "screening" in hz.label and "not a forecast" in hz.label.replace("not forecasts", "not a forecast")
        layers = hz.layers(30.0)
        assert layers["areas"], k


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        tok = app.state.rt.cfg.secrets.overwatch_token
        c.params = {"token": tok} if tok else {}  # type: ignore[assignment]
        yield c


def test_world_api_hazards_and_simulated_thermal(client):
    import time

    r = client.post("/api/incidents", json={"title": "flood", "mission_mode": "search", "scenario_id": "FR1"},
                    params=client.params)
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 15
    meta = {}
    while time.monotonic() < deadline:
        meta = client.get("/api/world/meta").json()
        if meta.get("name") == "flood":
            break
        time.sleep(0.3)
    assert meta["name"] == "flood" and meta["hazards"] and "w=flood" in meta["ortho"]
    hz = client.get("/api/world/hazards").json()
    assert hz["available"] and "screening" in hz["label"]
    th = None
    while time.monotonic() < deadline + 10:  # the first frame of the new world may still be rendering
        th = client.get("/api/world/thermal.jpg")
        if th.status_code == 200:
            break
        time.sleep(0.3)
    assert th.status_code == 200 and th.headers["content-type"] == "image/jpeg"
    assert len(client.get(meta["ortho"]).content) > 10_000
    w3d = client.get("/api/sim3d/world").json()
    assert w3d["available"] and w3d["hazards"]["kind"] == "flood"

    # Switch to FF1 and verify 3D fire hazards and building openings are served
    r2 = client.post("/api/incidents", json={"title": "fire", "mission_mode": "structure", "scenario_id": "FF1"},
                     params=client.params)
    assert r2.status_code == 200
    deadline2 = time.monotonic() + 15
    w3d_fire = {}
    while time.monotonic() < deadline2:
        w3d_fire = client.get("/api/sim3d/world").json()
        if (w3d_fire.get("hazards") or {}).get("kind") == "fire":
            break
        time.sleep(0.3)
    assert w3d_fire["hazards"]["kind"] == "fire"
    bldg = next(b for b in w3d_fire["boxes"] if b["id"] == "bldg-f")
    assert len(bldg["openings"]) == 6
