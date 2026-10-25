"""Local ENU <-> WGS84 conversion (equirectangular tangent plane; adequate for a < 1 km demo area)."""

from __future__ import annotations

import math
from typing import Protocol

EARTH_R = 6378137.0


class _Ref(Protocol):
    lat: float
    lon: float


def _rot(x: float, y: float, north_offset_deg: float) -> tuple[float, float]:
    th = math.radians(north_offset_deg)
    return x * math.cos(th) + y * math.sin(th), -x * math.sin(th) + y * math.cos(th)


def local_to_latlon(x: float, y: float, ref: _Ref, north_offset_deg: float = 0.0) -> tuple[float, float]:
    e, n = _rot(x, y, north_offset_deg)
    lat = ref.lat + math.degrees(n / EARTH_R)
    lon = ref.lon + math.degrees(e / (EARTH_R * math.cos(math.radians(ref.lat))))
    return lat, lon


def latlon_to_local(lat: float, lon: float, ref: _Ref, north_offset_deg: float = 0.0) -> tuple[float, float]:
    n = math.radians(lat - ref.lat) * EARTH_R
    e = math.radians(lon - ref.lon) * EARTH_R * math.cos(math.radians(ref.lat))
    return _rot(e, n, -north_offset_deg)


def true_to_local_heading(true_heading_deg: float, north_offset_deg: float) -> float:
    """Compass (true) heading -> heading in the local frame (clockwise from local +y)."""
    return (true_heading_deg - north_offset_deg) % 360.0
