import pytest

from overwatch.config import GeoRef
from overwatch.geometry.geo import latlon_to_local, local_to_latlon

REF = GeoRef(lat=30.2669, lon=-97.7729)


@pytest.mark.parametrize("offset", [0.0, 25.0, -90.0])
def test_roundtrip(offset):
    lat, lon = local_to_latlon(12.5, -7.25, REF, offset)
    x, y = latlon_to_local(lat, lon, REF, offset)
    assert (x, y) == pytest.approx((12.5, -7.25), abs=1e-6)


def test_north_is_positive_latitude():
    lat, lon = local_to_latlon(0.0, 100.0, REF)
    assert lat > REF.lat and lon == pytest.approx(REF.lon)
