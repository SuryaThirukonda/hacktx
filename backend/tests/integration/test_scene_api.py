"""Phase 12: scene asset upload, transform import, file serving, simulator export and the synthetic indoor sample."""

import json

import pytest
from fastapi.testclient import TestClient

from overwatch.api.app import create_app

PLY = b"ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n1 0 0\n0 1 0\n"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        tok = app.state.rt.cfg.secrets.overwatch_token
        c.params = {"token": tok} if tok else {}  # type: ignore[assignment]
        yield c


@pytest.fixture()
def inc(client):
    return client.post("/api/incidents", json={"title": "scene", "mission_mode": "indoor", "scenario_id": "DEMO-1"},
                       params=client.params).json()


def test_upload_ply_with_transform_and_overlay(client, inc):
    iid, enu = inc["id"], inc["coordinate_reference"]["enu_frame"]
    r = client.post(f"/api/incidents/{iid}/scene/assets", content=PLY,
                    params={**client.params, "name": "test cloud", "format": "ply", "origin": "unit test file"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["kind"] == "point_cloud" and a["points"] == 3 and a["frame"].startswith("scene:") and not a["reconstructed"]
    assert client.get(a["url"]).content == PLY
    client.post(f"/api/incidents/{iid}/scene/anchors", params=client.params,
                json={"name": "Hall", "type": "room", "position": {"frame": a["frame"], "x": 0.5, "y": 0.5}})
    client.post(f"/api/incidents/{iid}/observations", params=client.params,
                json={"category": "person", "label": "possible person", "provenance": "simulated",
                      "world_position": {"frame": enu, "x": 3.0, "y": 4.0}, "horizontal_uncertainty_m": 1.0,
                      "position_estimation_method": "ground_plane"})
    ov = client.get(f"/api/incidents/{iid}/scene/overlay", params={"frame": a["frame"]}).json()
    assert [i["label"] for i in ov["items"]] == ["Hall"] and enu in ov["unaligned_frames"]
    tf = {"matrix": [[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 0], [0, 0, 0, 1]], "method": "manual_3pt", "residual_m": 0.1}
    r = client.post(f"/api/scene-assets/{a['id']}/transform", content=json.dumps(tf), params=client.params)
    assert r.status_code == 200 and r.json()["transforms"][0]["target_frame"] == enu
    ov = client.get(f"/api/incidents/{iid}/scene/overlay", params={"frame": a["frame"]}).json()
    o = next(i for i in ov["items"] if i["kind"] == "observation")
    assert (o["position"]["x"], o["position"]["y"]) == (2.0, 2.0)  # ENU (3,4) minus the scan offset (1,2)
    assert ov["note"] is None


def test_bad_files_are_rejected(client, inc):
    r = client.post(f"/api/incidents/{inc['id']}/scene/assets", content=b"not a ply",
                    params={**client.params, "name": "x", "format": "ply"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_scene_file"
    r = client.post(f"/api/incidents/{inc['id']}/scene/assets", content=PLY,
                    params={**client.params, "name": "x", "format": "fbx"})
    assert r.status_code == 422


def test_simulator_export_is_labelled_and_exactly_aligned(client, inc):
    r = client.post(f"/api/incidents/{inc['id']}/scene/sim-export", params=client.params)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["provenance"] == "simulated" and not a["reconstructed"] and "not a reconstruction" in a["origin"]
    assert a["points"] > 1000 and a["transforms"][0]["method"].startswith("simulator")
    head = client.get(a["url"]).content[:200]
    assert b"not a reconstruction" in head
    again = client.post(f"/api/incidents/{inc['id']}/scene/sim-export", params=client.params).json()
    assert again["id"] == a["id"]  # exported once per incident


def test_synthetic_indoor_sample_is_labelled_and_unaligned(client, inc):
    a = client.post(f"/api/incidents/{inc['id']}/scene/sample-indoor", params=client.params).json()
    assert a["frame"].startswith("arkit:") and "SYNTHETIC" in a["origin"] and a["transforms"] == []
    assert b"SYNTHETIC" in client.get(a["url"]).content[:200]
