"""Phase 10: deterministic recommendations, model proposals through validation and approval, templated messages."""

import re

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


def actions(c, iid, oid=None):
    body = c.get(f"/api/incidents/{iid}/actions").json()
    return [a for a in body["items"] if oid is None or a["parameters"].get("observation_id") == oid]


@pytest.fixture()
def setup(client):
    inc = post(client, "/api/incidents", {"title": "recs", "mission_mode": "search"}).json()
    obs = post(client, f"/api/incidents/{inc['id']}/observations",
               {"category": "person", "label": "possible person", "sensor_source_id": "drone", "provenance": "simulated",
                "world_position": {"frame": inc["coordinate_reference"]["enu_frame"], "x": 4.0, "y": 7.0}, "horizontal_uncertainty_m": 2.3,
                "position_estimation_method": "ground_plane", "stale_after_s": 600}).json()
    return inc, obs


def numbers(s):
    return re.findall(r"\d+(?:\.\d+)?", s)


def test_rules_propose_review_then_notify_with_approval(client, setup):
    inc, obs = setup
    iid, oid = inc["id"], obs["id"]
    client.app_rt.incidents.run_missions()
    acts = actions(client, iid, oid)
    assert [a["action_type"] for a in acts] == ["review_candidate"]
    a = acts[0]
    assert a["proposed_by"] == "rules" and a["approval_status"] == "not_required"
    assert "a candidate is not a confirmation" in a["safety_constraints"]
    # every number in the message is a computed field with units
    f = a["parameters"]["facts"]
    allowed = {*f["observed_utc"].split(":"), f"{f['uncertainty_m']:.1f}"}
    assert set(numbers(a["parameters"]["message"])) <= allowed, a["parameters"]["message"]
    client.app_rt.incidents.run_missions()
    assert len(actions(client, iid, oid)) == 1  # deduplicated by key

    post(client, f"/api/observations/{oid}/review", {"status": "confirmed", "reviewer": "op1"})
    client.app_rt.incidents.run_missions()
    acts = {a["action_type"]: a for a in actions(client, iid, oid)}
    assert acts["review_candidate"]["execution_status"] == "done"
    n = acts["notify_responder"]
    assert n["approval_status"] == "pending" and "only confirmed observations are sent" in n["safety_constraints"]
    assert not client.get(f"/api/incidents/{iid}/alerts").json()["items"]  # nothing sent before approval
    r = post(client, f"/api/actions/{n['id']}/approve", {"by": "op1"})
    assert r.status_code == 200, r.text
    assert r.json()["approval_status"] == "approved" and r.json()["execution_status"] == "done"
    alerts = client.get(f"/api/incidents/{iid}/alerts").json()["items"]
    assert len(alerts) == 1 and alerts[0]["source_observation_ids"] == [oid]
    assert post(client, f"/api/actions/{n['id']}/approve").status_code == 400  # already decided
    kinds = [e["kind"] for e in client.get(f"/api/incidents/{iid}/events").json()["items"]]
    assert "decision_proposal" in kinds and "action_approved" in kinds


@pytest.mark.parametrize("body,code", [
    ({"action_type": "fly_to", "parameters": {}}, "not_allowed"),
    ({"action_type": "inspect_area", "parameters": {"observation_id": "X", "waypoint": "A"}}, "flight_control"),
    ({"action_type": "hold_for_more_information", "parameters": {"reason": "stale_observation"},
      "rationale": "pursue the subject"}, "flight_control"),
    ({"action_type": "review_candidate", "parameters": {"observation_id": "OBS", "range_m": 12}}, "schema"),
    ({"action_type": "hold_for_more_information", "parameters": {"reason": "stale_observation"},
      "rationale": "the person is 12 metres away"}, "model_measurement"),
    ({"action_type": "hold_for_more_information", "parameters": {"reason": "because I say so"}}, "schema"),
    ({"action_type": "show_structure_side", "parameters": {"building_id": "b", "side": "A"}}, "mission_rule"),
    ({"action_type": "review_candidate", "parameters": {"observation_id": "OBS-missing"}}, "missing_data"),
])
def test_invalid_model_proposals_are_rejected_and_logged(client, setup, body, code):
    inc, _ = setup
    r = post(client, f"/api/incidents/{inc['id']}/actions", {"proposed_by": "model", "engine": "openai", **body})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code
    ev = [e for e in client.get(f"/api/incidents/{inc['id']}/events").json()["items"] if e["kind"] == "proposal_rejected"]
    assert ev and ev[-1]["payload"]["code"] == code


def test_model_cannot_notify_an_unconfirmed_observation(client, setup):
    inc, obs = setup
    r = post(client, f"/api/incidents/{inc['id']}/actions",
             {"action_type": "notify_responder", "parameters": {"observation_id": obs["id"]}, "engine": "openai"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "missing_data"


def test_valid_model_proposal_waits_for_approval_and_can_be_rejected(client, setup):
    inc, obs = setup
    r = post(client, f"/api/incidents/{inc['id']}/actions",
             {"action_type": "inspect_area", "parameters": {"observation_id": obs["id"]}, "engine": "openai",
              "rationale": "a second look would help"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["proposed_by"] == "model" and a["approval_status"] == "pending"
    assert a["parameters"]["message"].startswith("Inspect the area around possible person (position within 2.3 m)")
    assert "real flight stays manual" in " ".join(a["safety_constraints"])
    r = post(client, f"/api/actions/{a['id']}/reject", {"by": "op1"})
    assert r.json()["approval_status"] == "rejected" and r.json()["execution_status"] == "cancelled"


def test_actions_endpoint_reports_decision_mode(client, setup):
    inc, _ = setup
    d = client.get(f"/api/incidents/{inc['id']}/actions").json()["decisions"]
    assert d is not None and d["mode_label"]
