"""Geospatial helpers for incidents: WGS84 <-> incident ENU, range and bearing, uncertainty, ground projection.

Never invents a location: every conversion that needs a reference (geographic origin, valid calibration) raises
``UnsupportedGeolocation`` instead of guessing, and callers show "location not yet verified".
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import numpy as np

from ..geometry.geo import latlon_to_local, local_to_latlon
from .models import Incident, Position

EARTH_R = 6371008.8  # mean Earth radius (m) for haversine


class UnsupportedGeolocation(ValueError):
    """The data needed for this location claim (origin, calibration, frame) is not available."""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from true north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def combined_uncertainty(*sigmas: float | None) -> float | None:
    """Root-sum-square of independent horizontal uncertainties; None if any is unknown."""
    if any(s is None for s in sigmas):
        return None
    return float(math.sqrt(sum(float(s) ** 2 for s in sigmas)))  # type: ignore[arg-type]


def _origin(inc: Incident) -> SimpleNamespace:
    ref = inc.coordinate_reference
    if ref is None or ref.origin_lat is None or ref.origin_lon is None:
        raise UnsupportedGeolocation("the incident has no geographic origin (WGS84 reference)")
    return SimpleNamespace(lat=ref.origin_lat, lon=ref.origin_lon, north=ref.north_offset_deg)


def enu_from_wgs84(inc: Incident, lat: float, lon: float) -> Position:
    o = _origin(inc)
    x, y = latlon_to_local(lat, lon, o, o.north)
    return Position(frame=inc.enu_frame, x=x, y=y)


def wgs84_from_enu(inc: Incident, p: Position) -> tuple[float, float]:
    p.require(inc.enu_frame)
    o = _origin(inc)
    return local_to_latlon(p.x, p.y, o, o.north)


def ground_from_image(calibration: Any, u: float, v: float) -> tuple[float, float, float]:
    """Image point -> ground (x, y) and the calibration's error bound, only with a valid image-to-ground transform."""
    if calibration is None or not getattr(calibration, "valid", False) or calibration.H_img_to_ground is None:
        reason = getattr(calibration, "reason", "no calibration") if calibration is not None else "no calibration"
        raise UnsupportedGeolocation(f"no valid image-to-ground transform ({reason})")
    if calibration.error_bound_m is None:
        raise UnsupportedGeolocation("the calibration has no error bound")
    H = np.asarray(calibration.H_img_to_ground, float)
    q = H @ np.array([u, v, 1.0])
    if abs(q[2]) < 1e-9:
        raise UnsupportedGeolocation("the image point is at or above the horizon")
    return float(q[0] / q[2]), float(q[1] / q[2]), float(calibration.error_bound_m)


def foot_point(box: tuple[float, float, float, float]) -> tuple[float, float]:
    """Where a standing person touches the ground in the image: bottom centre of the box."""
    x0, _, x1, y1 = box
    return (x0 + x1) / 2.0, y1


def location_report(inc: Incident, world_position: Position | None, uncertainty_m: float | None,
                    method: str) -> dict[str, Any]:
    if world_position is None:
        return {"verified": False, "text": "location not yet verified"}
    out: dict[str, Any] = {"verified": True, "enu": world_position.model_dump(), "uncertainty_m": uncertainty_m,
                           "method": method}
    try:
        lat, lon = wgs84_from_enu(inc, world_position)
        out["wgs84"] = {"lat": round(lat, 7), "lon": round(lon, 7)}
        out["origin"] = inc.coordinate_reference.note if inc.coordinate_reference else ""
    except UnsupportedGeolocation as e:
        out["wgs84"] = None
        out["wgs84_reason"] = str(e)
    return out
