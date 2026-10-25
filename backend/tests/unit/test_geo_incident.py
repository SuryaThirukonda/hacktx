import math

import pytest

from overwatch.incident.briefing import responder_targets
from overwatch.incident.geo import (UnsupportedGeolocation, bearing_deg, combined_uncertainty, enu_from_wgs84,
                                    foot_point, ground_from_image, haversine_m, location_report, wgs84_from_enu)
from overwatch.incident.models import CoordinateReference, Incident, Observation, Position, Review, utcnow
from overwatch.incident.store import IncidentStore
from overwatch.schemas import CalibrationStatus


def inc_with_origin(north=0.0, provenance="live"):
    inc = Incident(title="t", provenance=provenance)
    inc.coordinate_reference = CoordinateReference(enu_frame=inc.enu_frame, origin_lat=30.2669, origin_lon=-97.7729,
                                                   north_offset_deg=north)
    return inc


def test_haversine_and_bearing():
    # one degree of latitude is about 111.2 km; due east is 90 degrees
    assert haversine_m(30.0, -97.0, 31.0, -97.0) == pytest.approx(111195, rel=1e-3)
    assert bearing_deg(30.0, -97.0, 30.0, -96.9) == pytest.approx(90.0, abs=0.1)
    assert bearing_deg(30.0, -97.0, 29.9, -97.0) == pytest.approx(180.0, abs=0.1)


def test_enu_wgs84_round_trip_with_north_offset():
    for north in (0.0, 25.0):
        inc = inc_with_origin(north)
        p = Position(frame=inc.enu_frame, x=40.0, y=-15.0)
        lat, lon = wgs84_from_enu(inc, p)
        back = enu_from_wgs84(inc, lat, lon)
        assert back.x == pytest.approx(40.0, abs=0.05) and back.y == pytest.approx(-15.0, abs=0.05)
        assert haversine_m(30.2669, -97.7729, lat, lon) == pytest.approx(math.hypot(40, 15), rel=2e-3)


def test_no_origin_means_no_coordinates():
    inc = Incident(title="t")
    with pytest.raises(UnsupportedGeolocation):
        wgs84_from_enu(inc, Position(frame=inc.enu_frame, x=1, y=1))
    rep = location_report(inc, Position(frame=inc.enu_frame, x=1, y=1), 2.0, "ground_plane")
    assert rep["verified"] and rep["wgs84"] is None and "no geographic origin" in rep["wgs84_reason"]
    assert location_report(inc, None, None, "none") == {"verified": False, "text": "location not yet verified"}


def test_ground_projection_needs_a_valid_calibration():
    with pytest.raises(UnsupportedGeolocation, match="no valid"):
        ground_from_image(CalibrationStatus(valid=False, reason="not calibrated"), 10, 10)
    H = [[0.01, 0, -1.0], [0, -0.01, 5.0], [0, 0, 1]]
    cal = CalibrationStatus(valid=True, source="markers", H_img_to_ground=H, error_bound_m=0.4, reason="ok")
    x, y, err = ground_from_image(cal, *foot_point((100, 100, 140, 300)))
    assert (x, y, err) == pytest.approx((0.2, 2.0, 0.4))
    assert combined_uncertainty(0.3, 0.4) == pytest.approx(0.5) and combined_uncertainty(0.3, None) is None


def test_real_gps_is_not_placed_in_a_simulated_incident():
    st = IncidentStore()
    inc = st.create_incident(inc_with_origin(provenance="simulated"))
    o = st.add_observation(Observation(incident_id=inc.id, sensor_source_id="drone", observed_at=utcnow(),
                                       category="person", label="person", provenance="simulated",
                                       world_position=Position(frame=inc.enu_frame, x=5, y=5),
                                       horizontal_uncertainty_m=1.0, position_estimation_method="ground_plane"))
    st.review(Review(observation_id=o.id, status="confirmed", reviewer="op"))
    out = responder_targets(st, inc.id, {"lat": 30.2669, "lon": -97.7729, "accuracy_m": 5,
                                          "source": "browser_geolocation"})
    assert out["responder"]["grounded"] is False and "simulated incident" in out["responder"]["how"]
