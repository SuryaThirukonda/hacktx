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
                                       label="person", provenance="simulated", **kw))
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
