from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from overwatch.incident.models import (PROVENANCE_FROM_WIRE, Alert, BoundingBox, FrameMismatch, Incident,
                                       MissionAction, Observation, Position, Review, SceneAnchor, Transform, utcnow)
from overwatch.incident.store import IncidentStore, NotFound


def obs(inc: Incident, **kw) -> Observation:
    base = dict(incident_id=inc.id, sensor_source_id="sim-drone", observed_at=utcnow(), category="person",
                label="possible person", provenance="simulated")
    base.update(kw)
    return Observation(**base)


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        Incident(title="x", created_at=datetime(2026, 10, 24, 12, 0))
    inc = Incident(title="x", created_at=datetime(2026, 10, 24, 7, 0, tzinfo=timezone(timedelta(hours=-5))))
    assert inc.created_at.utcoffset() == timedelta(0) and inc.created_at.hour == 12


def test_unknown_or_unnamed_frames_are_rejected():
    with pytest.raises(ValidationError):
        Position(frame="gps", x=0, y=0)
    with pytest.raises(ValidationError):
        Position(frame="enu", x=0, y=0)  # an ENU frame must name its incident
    assert Position(frame="wgs84", x=-97.7, y=30.3).frame == "wgs84"


def test_position_requires_uncertainty_and_method():
    inc = Incident(title="t")
    p = Position(frame=inc.enu_frame, x=3, y=4)
    with pytest.raises(ValidationError):
        obs(inc, world_position=p)  # no uncertainty
    with pytest.raises(ValidationError):
        obs(inc, world_position=p, horizontal_uncertainty_m=1.0)  # no method
    o = obs(inc, world_position=p, horizontal_uncertainty_m=1.0, position_estimation_method="ground_plane")
    assert o.coordinate_frame == inc.enu_frame
    with pytest.raises(ValidationError):
        obs(inc, horizontal_uncertainty_m=1.0)  # uncertainty without a position


def test_no_position_reads_as_unverified():
    inc = Incident(title="t")
    o = obs(inc)
    assert o.world_position is None and o.location_text == "location not yet verified"


def test_manual_positions_need_manual_provenance():
    inc = Incident(title="t")
    p = Position(frame=inc.enu_frame, x=1, y=1)
    with pytest.raises(ValidationError):
        obs(inc, world_position=p, horizontal_uncertainty_m=2, position_estimation_method="manual")
    o = obs(inc, world_position=p, horizontal_uncertainty_m=2, position_estimation_method="manual", provenance="manual")
    assert o.provenance == "manual"


def test_bounding_box_must_use_its_sources_image_frame():
    inc = Incident(title="t")
    with pytest.raises(ValidationError):
        obs(inc, bounding_box=BoundingBox(frame="image:other", x0=0, y0=0, x1=10, y1=10))
    o = obs(inc, bounding_box=BoundingBox(frame="image:sim-drone", x0=0, y0=0, x1=10, y1=10))
    assert o.bounding_box is not None


def test_transform_refuses_the_wrong_frame_and_applies_rigid_motion():
    tf = Transform.from_rt("arkit:scan1", "enu:INC-1", yaw_deg=90, t=(10, 0, 0), method="manual_3pt")
    out = tf.apply(Position(frame="arkit:scan1", x=1, y=0, z=0))
    assert out.frame == "enu:INC-1" and abs(out.x - 10) < 1e-9 and abs(out.y - 1) < 1e-9
    with pytest.raises(FrameMismatch):
        tf.apply(Position(frame="scene:other", x=1, y=0))
    with pytest.raises(ValidationError):
        Transform(source_frame="wgs84", target_frame="enu:INC-1", matrix=[[1, 0, 0, 0]] * 3 + [[0, 0, 0, 1]],
                  method="x")


def test_anchor_frame_must_match_its_position():
    with pytest.raises(ValidationError):
        SceneAnchor(incident_id="I", name="door", type="door", coordinate_frame="enu:I",
                    position=Position(frame="arkit:s", x=0, y=0))


def test_store_round_trip_and_event_log(tmp_path):
    st = IncidentStore(tmp_path / "ow.db")
    inc = st.create_incident(Incident(title="Training area", scenario_id="DEMO-1"))
    o = st.add_observation(obs(inc, confidence=0.81, detector="sim"))
    assert st.get_observation(o.id) == o
    assert [v.review_status for v in st.list_observations(inc.id)] == ["pending"]
    st.set_mission(inc.id, "structure")
    assert st.get_incident(inc.id).mission_mode == "structure"
    kinds = [e.kind for e in st.events(inc.id)]
    assert kinds == ["incident_created", "observation", "mission_changed"]
    seqs = [e.seq for e in st.events(inc.id)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    st.close()
    reopened = IncidentStore(tmp_path / "ow.db")
    assert reopened.get_incident(inc.id).title == "Training area"
    with pytest.raises(NotFound):
        reopened.get_incident("nope")


def test_raw_observation_is_untouched_by_reviews():
    st = IncidentStore()
    inc = st.create_incident(Incident(title="t"))
    o = st.add_observation(obs(inc, label="possible person"))
    v = st.review(Review(observation_id=o.id, status="confirmed", reviewer="op1", corrected_label="person"))
    assert v.review_status == "confirmed" and v.reviewer == "op1"
    assert st.get_observation(o.id).label == "possible person"  # raw row unchanged
    st.review(Review(observation_id=o.id, status="rejected", reviewer="op2"))
    assert st.list_observations(inc.id)[0].review_status == "rejected"  # latest review wins
    assert st.list_observations(inc.id, status="confirmed") == []


def test_provenance_survives_storage_and_wire_mapping():
    assert PROVENANCE_FROM_WIRE == {"LIVE": "live", "RECORDED": "replay", "SYNTHETIC": "simulated"}
    st = IncidentStore()
    inc = st.create_incident(Incident(title="t"))
    for prov in ("live", "replay", "simulated"):
        st.add_observation(obs(inc, provenance=prov))
    assert sorted(v.observation.provenance for v in st.list_observations(inc.id)) == ["live", "replay", "simulated"]


def test_stale_observations_are_flagged():
    st = IncidentStore()
    inc = st.create_incident(Incident(title="t"))
    t0 = utcnow() - timedelta(minutes=5)
    st.add_observation(obs(inc, observed_at=t0, stale_after=t0 + timedelta(seconds=60)))
    assert st.list_observations(inc.id)[0].stale is True


def test_alert_ack_and_actions():
    st = IncidentStore()
    inc = st.create_incident(Incident(title="t"))
    a = st.create_alert(Alert(incident_id=inc.id, severity="attention", message="Possible person detected. "
                              "Awaiting confirmation."))
    st.mark_delivered(a.id)
    acked = st.acknowledge_alert(a.id, by="responder-1")
    assert acked.status == "acknowledged" and acked.acknowledged_at is not None
    assert st.acknowledge_alert(a.id).acknowledged_by == "responder-1"  # idempotent
    act = st.add_action(MissionAction(incident_id=inc.id, mission="search", action_type="review_candidate",
                                      proposed_by="rules"))
    assert st.get_action(act.id).approval_status == "pending"
    kinds = [e.kind for e in st.events(inc.id)]
    assert kinds[-4:] == ["alert", "alert_delivered", "alert_acknowledged", "decision_proposal"]


def test_listener_failure_does_not_block_persistence():
    st = IncidentStore()
    st.subscribe(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
    inc = st.create_incident(Incident(title="t"))
    assert st.get_incident(inc.id).title == "t"
