from datetime import timedelta

from overwatch.incident.briefing import cardinal, clock_of, responder_targets
from overwatch.incident.models import CoordinateReference, Incident, Observation, Position, Review, utcnow
from overwatch.incident.store import IncidentStore


def setup(origin=False):
    st = IncidentStore()
    inc = Incident(title="t")
    if origin:
        inc.coordinate_reference = CoordinateReference(enu_frame=inc.enu_frame, origin_lat=30.2849, origin_lon=-97.7341)
    st.create_incident(inc)
    return st, inc


def person(st, inc, xy=None, unc=1.5, age_s=10.0, confirm=True):
    t = utcnow() - timedelta(seconds=age_s)
    kw = {}
    if xy is not None:
        kw = dict(world_position=Position(frame=inc.enu_frame, x=xy[0], y=xy[1]), horizontal_uncertainty_m=unc,
                  position_estimation_method="ground_plane")
    o = st.add_observation(Observation(incident_id=inc.id, sensor_source_id="drone", observed_at=t, category="person",
                                       label="person", provenance="simulated", stale_after=t + timedelta(seconds=120), **kw))
    if confirm:
        st.review(Review(observation_id=o.id, status="confirmed", reviewer="op"))
    return o


def test_helpers():
    assert cardinal(0) == "north" and cardinal(90) == "east" and cardinal(225) == "south-west"
    assert clock_of(0) == 12 and clock_of(90) == 3 and clock_of(-60) == 10


def test_no_bearing_without_a_grounded_responder():
    st, inc = setup()
    person(st, inc, xy=(10, 0))
    out = responder_targets(st, inc.id, None)
    t = out["targets"][0]
    assert t["grounded"] is False and "range_m" not in t and "no bearing or distance" in t["text"]


def test_gps_without_a_geographic_origin_is_not_placed():
    st, inc = setup(origin=False)
    person(st, inc, xy=(10, 0))
    out = responder_targets(st, inc.id, {"lat": 30.2849, "lon": -97.7341, "accuracy_m": 5})
    assert out["responder"]["grounded"] is False and "no geographic origin" in out["responder"]["how"]
    assert out["targets"][0]["grounded"] is False


def test_target_without_position_says_refer_to_video():
    st, inc = setup()
    person(st, inc, xy=None, age_s=35)
    t = responder_targets(st, inc.id, {"enu": {"frame": inc.enu_frame, "x": 0, "y": 0}, "accuracy_m": 1})["targets"][0]
    assert t["text"] == "Person: position unavailable. Refer to video. Last observed 35 seconds ago."


def test_cardinal_without_trusted_heading_and_clock_with_it():
    st, inc = setup()
    person(st, inc, xy=(30, 40), unc=2.0)
    base = {"enu": {"frame": inc.enu_frame, "x": 0, "y": 0}, "accuracy_m": 1.5}
    t = responder_targets(st, inc.id, {**base, "heading_deg": 90, "heading_accuracy_deg": 45})["targets"][0]
    assert t["grounded"] and t["range_m"] == 50.0 and "clock" not in t and t["cardinal"] == "north-east"
    assert t["uncertainty_m"] == 2.5 and "plus or minus 2" in t["text"]
    t = responder_targets(st, inc.id, {**base, "heading_deg": 0, "heading_accuracy_deg": 10})["targets"][0]
    assert t["clock"] == 1  # bearing 37 deg with the responder facing north


def test_gps_with_origin_is_converted_into_the_incident_frame():
    st, inc = setup(origin=True)
    person(st, inc, xy=(0, 111.0))
    out = responder_targets(st, inc.id, {"lat": 30.2849, "lon": -97.7341, "accuracy_m": 5})
    t = out["targets"][0]
    assert out["responder"]["grounded"] and t["grounded"] and abs(t["range_m"] - 111.0) < 0.5 and t["cardinal"] == "north"


def test_only_confirmed_or_manual_targets_are_briefed():
    st, inc = setup()
    person(st, inc, xy=(5, 5), confirm=False)
    rej = person(st, inc, xy=(6, 6), confirm=False)
    st.review(Review(observation_id=rej.id, status="rejected", reviewer="op"))
    assert responder_targets(st, inc.id, None)["targets"] == []


def test_stale_fix_and_stale_target_do_not_emit_relative_callouts():
    st, inc = setup()
    person(st, inc, xy=(30, 40))
    base = {"enu": {"frame": inc.enu_frame, "x": 0, "y": 0}, "accuracy_m": 1,
            "heading_deg": 0, "heading_accuracy_deg": 5,
            "received_at": (utcnow() - timedelta(seconds=20)).isoformat()}
    out = responder_targets(st, inc.id, base)
    assert not out["responder"]["grounded"] and not out["responder"]["heading_trusted"]
    assert "range_m" not in out["targets"][0]
    old = person(st, inc, xy=(3, 4), age_s=1000)
    base["received_at"] = utcnow().isoformat()
    assert "range_m" not in next(t for t in responder_targets(st, inc.id, base)["targets"] if t["observation_id"] == old.id)


def test_unknown_uncertainty_and_negative_heading_accuracy_are_not_trusted():
    st, inc = setup()
    person(st, inc, xy=(30, 40))
    base = {"enu": {"frame": inc.enu_frame, "x": 0, "y": 0}, "heading_deg": 0, "heading_accuracy_deg": -1}
    out = responder_targets(st, inc.id, base)
    assert "range_m" not in out["targets"][0] and not out["responder"]["heading_trusted"]


def test_rotated_incident_map_uses_true_north_for_callouts():
    st, inc = setup(origin=True)
    inc.coordinate_reference.north_offset_deg = 90
    st.update_incident(inc)
    person(st, inc, xy=(0, 50))
    out = responder_targets(st, inc.id, {"enu": {"frame": inc.enu_frame, "x": 0, "y": 0},
                           "accuracy_m": 1, "heading_deg": 90, "heading_accuracy_deg": 5})
    target = out["targets"][0]
    assert target["cardinal"] == "east" and target["clock"] == 12


def test_confirmed_manual_target_is_not_duplicated_and_rejected_manual_is_hidden():
    st, inc = setup()
    o = st.add_observation(Observation(incident_id=inc.id, sensor_source_id="operator", category="person",
                                      label="manual person", provenance="manual", observed_at=utcnow()))
    st.review(Review(observation_id=o.id, status="confirmed", reviewer="op"))
    assert len(responder_targets(st, inc.id, None)["targets"]) == 1
    st.review(Review(observation_id=o.id, status="rejected", reviewer="op"))
    assert responder_targets(st, inc.id, None)["targets"] == []


def test_unaligned_scan_coordinates_are_not_drawn_on_incident_map():
    st, inc = setup()
    o = st.add_observation(Observation(incident_id=inc.id, sensor_source_id="operator", observed_at=utcnow(),
                                      category="person", label="scan annotation", provenance="manual",
                                      world_position=Position(frame="arkit:local-scan", x=1, y=2),
                                      horizontal_uncertainty_m=0.5, position_estimation_method="anchor"))
    target = responder_targets(st, inc.id, None)["targets"][0]
    assert target["observation_id"] == o.id and "map_position" not in target and "range_m" not in target
    assert "unaligned" in target["text"]
