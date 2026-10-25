import math

from .test_target_acquisition import setup_scene


def assistance_scene(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch, scenario="SAR1")
    # Designation, not an actor distress label, excludes this measured person.
    rt.pipeline.lock_track("responder", rt.pipeline.raw_tracks[1].track_id)
    for n in range(7, 14):
        frame(n)
        rt.incidents.run_missions()
    items = c.get(f"/api/incidents/{iid}/sar").json()["items"]
    assert len(items) == 1
    return c, rt, iid, frame, items[0]


def test_review_confirmation_guidance_delivery_ack_and_backlog(monkeypatch):
    c, rt, iid, frame, item = assistance_scene(monkeypatch)
    obs = item["observation"]
    assert item["review_status"] == "pending" and item["dispatch"] is None
    assert obs["evidence_frame_reference"] and obs["attributes"]["sar"]["environment"]["source"] == "simulated environmental map"
    base = f"/api/incidents/{iid}/sar/{obs['id']}/review"
    body = {"status": "confirmed", "dispatch": True, "reviewer": "human test operator", "session_id": rt.pipeline.session_id}
    response = c.post(base, json=body)
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    guidance, alert = item["guidance"], item["dispatch"]
    assert guidance["mode"] == "simulated_navigation" and alert["status"] == "pending"
    assert "Simulation" in alert["message"] and "confirmed" in alert["message"]
    target = obs["world_position"]
    start = guidance["responder_position"]
    assert guidance["distance_m"] == round(math.hypot(target["x"]-start["x"], target["y"]-start["y"]), 1)
    expected_bearing = math.degrees(math.atan2(target["x"]-start["x"], target["y"]-start["y"])) % 360
    assert guidance["bearing_deg"] == round(expected_bearing)
    assert guidance["route"]["status"] in ("approach_only", "access_route")
    assert guidance["route"]["max_water_depth_m"] <= rt.cfg.sar.route_max_depth_m
    # Confirm retries neither dispatch nor review twice.
    assert c.post(base, json=body).status_code == 200
    assert len([a for a in rt.incidents.store.list_alerts(iid) if obs["id"] in a.source_observation_ids]) == 1
    aid = alert["id"]
    assert c.post(f"/api/alerts/{aid}/delivered").json()["status"] == "delivered"
    one = c.post(f"/api/alerts/{aid}/acknowledge", json={"by": "responder"}).json()
    two = c.post(f"/api/alerts/{aid}/acknowledge", json={"by": "responder"}).json()
    assert one["acknowledged_at"] == two["acknowledged_at"]
    with c.websocket_connect(f"/ws/incidents/{iid}") as ws:
        backlog = ws.receive_json()
        assert backlog["type"] == "backlog"
        assert any(e["kind"] == "alert_acknowledged" for e in backlog["items"])
    assert rt.pipeline.autonomy.mode == "MANUAL" and not rt.cfg.autonomy.allow_real_flight


def test_rejected_candidate_stays_traceable_without_dispatch_or_duplicates(monkeypatch):
    c, rt, iid, frame, item = assistance_scene(monkeypatch)
    oid = item["observation"]["id"]
    response = c.post(f"/api/incidents/{iid}/sar/{oid}/review", json={
        "status": "rejected", "dispatch": True, "reviewer": "operator", "session_id": rt.pipeline.session_id})
    assert response.status_code == 200
    for n in range(14, 20):
        frame(n)
        rt.incidents.run_missions()
    items = c.get(f"/api/incidents/{iid}/sar").json()["items"]
    assert len(items) == 1 and items[0]["review_status"] == "rejected"
    assert not rt.incidents.store.list_alerts(iid)
    assert not any(a.action_type == "notify_responder" for a in rt.incidents.store.list_actions(iid))


def test_missing_shared_frame_degrades_and_stale_or_old_session_cannot_dispatch(monkeypatch):
    c, rt, iid, frame, item = assistance_scene(monkeypatch)
    oid = item["observation"]["id"]
    base = f"/api/incidents/{iid}/sar/{oid}/review"
    body = {"status": "confirmed", "dispatch": False, "session_id": rt.pipeline.session_id}
    assert c.post(base, json={**body, "session_id": "old-session"}).status_code == 409
    rt.pipeline.counters["drone"].last_rx_ms -= 2000
    assert c.post(base, json=body).status_code == 409
    frame(14)
    assert c.post(base, json=body).status_code == 200
    rt.incidents.set_responder(iid, {"source": "simulated", "accuracy_m": .5,
        "enu": {"frame": "enu:another-incident", "x": 5, "y": 10}, "heading_deg": 0, "heading_accuracy_deg": 5})
    guidance = rt.incidents.targets.sar.guidance(rt.incidents.store.get_observation(oid))
    assert "distance_m" not in guidance and guidance["route"]["points"] == []
    assert "unverified" in guidance["callout"]


def test_sar_is_disabled_in_live_mode(monkeypatch):
    c, rt, iid, frame = setup_scene(monkeypatch, "LIVE")
    for n in range(7, 12):
        frame(n)
        rt.incidents.run_missions()
    assert c.get(f"/api/incidents/{iid}/sar").json()["items"] == []


def test_rescue_memory_survives_occlusion_reconnection_without_duplicate_candidate(monkeypatch):
    c, rt, iid, frame, item = assistance_scene(monkeypatch)
    key = item["observation"]["attributes"]["sar"]["logical_subject_id"]
    p = rt.pipeline
    boxes = p.detector.boxes
    p.detector.boxes = []
    for n in range(14, 21):
        frame(n)
        rt.incidents.run_missions()
    hidden = next(m for m in rt.incidents.store.list_targets(iid) if m["logical_subject_id"] == key)
    assert hidden["measured_position"] is None and hidden["last_verified_position"]
    assert hidden["position_kind"] == "predicted"
    p.detector.boxes = boxes
    for n in range(21, 28):
        frame(n)
        rt.incidents.run_missions()
    items = c.get(f"/api/incidents/{iid}/sar").json()["items"]
    assert len(items) == 1
    current = next(m for m in rt.incidents.store.list_targets(iid) if m["logical_subject_id"] == key)
    assert current["state"] == "VISIBLE" and current["measured_position"] is not None


def test_guidance_uses_current_measurement_between_persisted_snapshots(monkeypatch):
    c, rt, iid, frame, item = assistance_scene(monkeypatch)
    oid = item["observation"]["id"]
    body = {"status": "confirmed", "dispatch": False, "session_id": rt.pipeline.session_id}
    assert c.post(f"/api/incidents/{iid}/sar/{oid}/review", json=body).status_code == 200
    for n in range(14, 29):
        frame(n)
        # Refresh current target state at the normal publication cadence, without
        # manufacturing mission/persistence updates for every frame.
        rt.incidents.targets.update()
        guidance = rt.incidents.targets.sar.guidance(rt.incidents.store.get_observation(oid))
        assert "distance_m" in guidance, guidance["callout"]
        assert guidance["position_kind"] == "measured"
