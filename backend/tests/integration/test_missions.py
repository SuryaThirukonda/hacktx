import time

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


def wait_for(fn, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(0.4)
    return None


def test_search_flags_candidates_once_per_track(client):
    inc = post(client, "/api/incidents", {"title": "search", "mission_mode": "search", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    svc = client.app_rt.incidents

    def persons():
        svc.run_missions()
        return [v for v in client.get(f"/api/incidents/{iid}/observations").json()["items"]
                if v["observation"]["category"] == "person"]

    got = wait_for(lambda: len(persons()) >= 1 and persons())
    assert got, "Search flagged nothing"
    o = got[0]["observation"]
    assert o["label"] == "possible person" and o["attributes"]["flagged_by"] == "search_rules"
    assert got[0]["review_status"] == "pending" and o["provenance"] == "simulated"
    n = len(persons())
    for _ in range(3):
        svc.run_missions()
    assert len(persons()) == n  # the same tracks are not flagged again


def test_structure_proposes_openings_and_groups_by_side(client):
    inc = post(client, "/api/incidents", {"title": "structure", "mission_mode": "structure", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    svc = client.app_rt.incidents

    def openings():
        svc.run_missions()
        return [v for v in client.get(f"/api/incidents/{iid}/observations").json()["items"]
                if v["observation"]["category"] == "opening"]

    ops = wait_for(lambda: openings())
    assert ops, "no openings proposed"
    labels = {v["observation"]["label"] for v in ops}
    assert "possible doorway" in labels
    o = ops[0]["observation"]
    assert o["provenance"] == "simulated" and o["confidence"] is None and "fixture" in o["detector"]
    view = client.get(f"/api/incidents/{iid}/structure").json()
    b = view["buildings"][0]
    assert b["side_a"] is None and b["sides"]["?"]  # sides unknown until the operator sets side A
    view = post(client, f"/api/incidents/{iid}/structure/side-a", {"building_id": b["building_id"], "compass": "S"}).json()
    sides = view["buildings"][0]["sides"]
    texts = [x["text"] for s in "ABCD" for x in sides[s]]
    assert any(t.startswith("possible doorway, Side A, pending review") for t in texts), texts
    post(client, f"/api/incidents/{iid}/observations",
         {"category": "opening", "label": "blocked entrance", "provenance": "manual",
          "attributes": {"side": "D", "blocked": True}})
    sides = client.get(f"/api/incidents/{iid}/structure").json()["buildings"][0]["sides"]
    assert any(x["blocked"] and x["status"] == "manual" for x in sides["D"])
    assert "safe" in view["note"]  # and it says it never declares entry safe
    r = post(client, f"/api/incidents/{iid}/structure/side-a", {"building_id": "nope", "compass": "S"})
    assert r.status_code == 400


def test_structure_fixture_is_not_reproposed_after_restart(client):
    inc = post(client, "/api/incidents", {"title": "structure2", "mission_mode": "structure", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    svc = client.app_rt.incidents

    def openings():
        svc.run_missions()
        return [v["observation"] for v in client.get(f"/api/incidents/{iid}/observations").json()["items"]
                if v["observation"]["category"] == "opening"]

    first = wait_for(lambda: openings())
    assert first and all(o["attributes"].get("opening_id") for o in first)
    assert all(o["evidence_frame_reference"] for o in first)  # the drone frame the fixture proposed it from
    svc.proposed_openings.clear()  # what a backend restart loses
    for _ in range(3):
        svc.run_missions()
    ids = [o["attributes"]["opening_id"] for o in openings()]
    assert len(ids) == len(set(ids)), ids


def test_follow_designation_trail_and_release(client):
    inc = post(client, "/api/incidents", {"title": "follow", "mission_mode": "follow", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    svc = client.app_rt.incidents
    assert client.get(f"/api/incidents/{iid}/follow").json()["status"] == "none"
    tid = wait_for(lambda: next((t["track_id"] for t in client.get("/api/tracks").json()
                                 if t["state"] == "CONFIRMED" and t["class_name"] == "person"), None))
    assert tid
    r = post(client, f"/api/incidents/{iid}/follow/designate", {"track_id": tid, "by": "op1"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "tracking" and r.json()["designated_by"] == "op1"

    def trail():
        svc.run_missions()
        return len(client.get(f"/api/incidents/{iid}/follow").json()["trail"]) >= 3

    assert wait_for(trail), "no trail points recorded"
    v = client.get(f"/api/incidents/{iid}/follow").json()
    assert all(p["identity"] == "confirmed" for p in v["trail"] if p["track_id"] == tid)
    assert "valid" in v["motion"]
    assert post(client, f"/api/incidents/{iid}/follow/confirm").status_code == 400  # nothing unverified to confirm
    assert post(client, f"/api/incidents/{iid}/follow/designate", {"track_id": "drone:999:e9"}).status_code == 404
    post(client, f"/api/incidents/{iid}/follow/release", {"by": "op1"})
    kinds = [e["kind"] for e in client.get(f"/api/incidents/{iid}/events?limit=10000").json()["items"]]
    assert "follow_designated" in kinds and "follow_point" in kinds and "follow_released" in kinds


def test_operator_inputs_look_for_and_describe(client):
    inc = post(client, "/api/incidents", {"title": "pursuit", "mission_mode": "follow", "scenario_id": "DEMO-1"}).json()
    iid = inc["id"]
    r = post(client, f"/api/incidents/{iid}/look-for", {"text": "red shirt, jeans"}).json()
    assert r["look_for"]["text"] == "red shirt, jeans"
    assert client.app_rt.pipeline.look_for["text"] == "red shirt, jeans"
    assert post(client, f"/api/incidents/{iid}/look-for", {"text": ""}).json()["look_for"] is None
    assert post(client, f"/api/incidents/{iid}/follow/describe", {"text": " "}).status_code == 422
    wait_for(lambda: any(t["state"] == "CONFIRMED" for t in client.get("/api/tracks").json()))
    r = post(client, f"/api/incidents/{iid}/follow/describe", {"text": "blue shirt"})
    assert r.status_code == 200 and "ok" in r.json()
    client.app_rt.pipeline.describe_proposal = None
    assert post(client, f"/api/incidents/{iid}/follow/describe/confirm").status_code == 409  # nothing to confirm
    kinds = [e["kind"] for e in client.get(f"/api/incidents/{iid}/events?limit=10000").json()["items"]]
    assert "look_for" in kinds and "follow_describe" in kinds


def test_pursuit_demo_scripted_lock_shows_in_follow_and_drone_follows(client):
    """PJ1 (the runner followed low by the X2; the drone starts airborne): the scripted demo operator's lock is the
    Follow designation, labelled as scripted, and the simulator autonomy follows. (PP0 now starts on the pad and
    dispatches: its match arrives a minute in, which is covered by the headless scenario run.)"""
    inc = post(client, "/api/incidents", {"title": "pursuit demo", "mission_mode": "follow", "scenario_id": "PJ1"}).json()
    iid = inc["id"]
    svc = client.app_rt.incidents

    def designated():
        svc.run_missions()
        v = client.get(f"/api/incidents/{iid}/follow").json()
        return v if v["status"] == "tracking" else None

    v = wait_for(designated, timeout=30.0)
    assert v, "the scripted lock never became the Follow designation"
    assert v["designated_by"] == "scripted operator (demo)" and v["designation"] == 1
    auto = wait_for(lambda: (a := client.app_rt.pipeline.autonomy.state()).mode == "FOLLOW" and a, timeout=20.0)
    assert auto and auto.fsm in ("TRACKING", "COAST")
    svc.run_missions()
    assert client.get(f"/api/incidents/{iid}/follow").json()["designation"] == 1  # adopted once, not every tick
