"""Deterministic safety supervisor (Overwatch §5.5). It overrides everything, including decision models.

Checks: speed caps, altitude floor/ceiling, geofence (predicted 1.5 s ahead; outward motion removed), people-over
avoidance (never directly over people), battery warn/RTL, link health. Every clamp is recorded for the audit log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..config import AutonomyCfg
from ..schemas import SafetyStatus


def point_segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / max(float(np.dot(ab, ab)), 1e-12), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def inside(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    c = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            c = not c
        j = i
    return c


def fence_margin(poly: list[tuple[float, float]], x: float, y: float) -> float:
    """Signed distance to the fence: positive inside, negative outside."""
    if len(poly) < 3:
        return float("inf")
    p = np.array([x, y])
    d = min(point_segment_distance(p, np.array(poly[i]), np.array(poly[(i + 1) % len(poly)])) for i in range(len(poly)))
    return d if inside(poly, x, y) else -d


@dataclass
class DroneSafetyState:
    xy: tuple[float, float] | None
    alt_m: float | None
    heading_deg: float | None
    battery_pct: float | None
    link_ok: bool


@dataclass
class SafetyResult:
    forward: float
    right: float
    up: float
    yaw_rate: float
    status: SafetyStatus
    events: list[dict] = field(default_factory=list)
    request_rtl: bool = False


class SafetySupervisor:
    def __init__(self, cfg: AutonomyCfg) -> None:
        self.cfg = cfg
        self._battery_warned = False

    def check_viewpoint(self, xy: tuple[float, float], path: list[tuple[float, float]], alt_m: float,
                        people_xy: list[tuple[float, float]]) -> tuple[bool, float, str | None]:
        """Before a viewpoint is offered: (allowed, penalty, why). The same limits as ``apply``: a route or a
        viewpoint outside the geofence or outside the altitude band is rejected; a viewpoint close to the fence or
        over people (the supervisor would push the drone aside there) is penalised."""
        c = self.cfg
        if not (c.min_altitude_m <= alt_m <= c.max_altitude_m):
            return False, math.inf, f"altitude {alt_m:.0f} m outside {c.min_altitude_m:.0f}-{c.max_altitude_m:.0f} m"
        penalty, why = 0.0, None
        if c.geofence:
            for p in list(path) + [xy]:
                if fence_margin(c.geofence, float(p[0]), float(p[1])) < 2.0:
                    return False, math.inf, "leaves the geofence"
            m = fence_margin(c.geofence, float(xy[0]), float(xy[1]))
            if m < 8.0:
                penalty += 0.04 * (8.0 - m) / 6.0
                why = f"{m:.0f} m from the geofence"
        if any(math.dist(xy, q) < c.people_over_radius_m for q in people_xy):
            penalty += 0.02
            why = "people below the viewpoint (the supervisor holds the drone off them)"
        return True, penalty, why

    def apply(self, forward: float, right: float, up: float, yaw_rate: float, st: DroneSafetyState,
              people_xy: list[tuple[float, float]], reacquiring: bool) -> SafetyResult:
        c = self.cfg
        clamps: list[str] = []
        events: list[dict] = []
        vmax = c.reacquire_max_speed_mps if reacquiring else c.follow_max_speed_mps
        sp = math.hypot(forward, right)
        if sp > vmax:
            forward, right = forward * vmax / sp, right * vmax / sp
            clamps.append(f"speed {sp:.1f}->{vmax:.1f} m/s")
        if abs(yaw_rate) > c.max_yaw_rate_dps:
            clamps.append(f"yaw {yaw_rate:.0f}->{math.copysign(c.max_yaw_rate_dps, yaw_rate):.0f} deg/s")
            yaw_rate = math.copysign(c.max_yaw_rate_dps, yaw_rate)
        if abs(up) > c.max_vertical_mps:
            up = math.copysign(c.max_vertical_mps, up)
            clamps.append("vertical speed cap")
        alt_ok = True
        if st.alt_m is not None:
            if st.alt_m < c.min_altitude_m and up < 0.8:
                up = 0.8
                alt_ok = False
                clamps.append(f"altitude floor {c.min_altitude_m:.0f} m")
            elif st.alt_m > c.max_altitude_m and up > -0.8:
                up = -0.8
                alt_ok = False
                clamps.append(f"altitude ceiling {c.max_altitude_m:.0f} m")
        margin = None
        fence_ok = True
        people_over = False
        if st.xy is not None and st.heading_deg is not None:
            h = math.radians(st.heading_deg)
            fwd = np.array([math.sin(h), math.cos(h)])
            rgt = np.array([math.cos(h), -math.sin(h)])
            v = fwd * forward + rgt * right
            p = np.array(st.xy)
            if c.geofence:
                margin = fence_margin(c.geofence, *p)
                ahead = p + v * 1.5
                if fence_margin(c.geofence, *ahead) < 1.0:
                    # remove the velocity component that leaves the fence (towards the nearest edge outward normal)
                    cen = np.mean(np.array(c.geofence), axis=0)
                    inward = cen - p
                    inward /= max(np.linalg.norm(inward), 1e-9)
                    out_comp = -float(np.dot(v, inward))
                    if out_comp > 0:
                        v = v + inward * out_comp
                    if margin < 0:
                        v = inward * 1.5
                    fence_ok = margin >= 0
                    clamps.append("geofence: outward motion removed")
                    events.append({"kind": "geofence", "level": "warning", "text": f"Geofence: holding at fence (margin {margin:.1f} m)"})
            # never directly over people: push laterally away from anyone under the nadir disc
            for q in people_xy:
                d = p - np.array(q)
                dist = float(np.linalg.norm(d))
                if dist < c.people_over_radius_m:
                    people_over = True
                    away = d / max(dist, 1e-3)
                    toward = -float(np.dot(v, away))
                    if toward > 0:
                        v = v + away * toward
                    v = v + away * 1.2
                    clamps.append("people-over avoidance")
            forward, right = float(np.dot(v, fwd)), float(np.dot(v, rgt))
            sp = math.hypot(forward, right)
            if sp > vmax:
                forward, right = forward * vmax / sp, right * vmax / sp
        battery = "unknown"
        rtl = False
        if st.battery_pct is not None:
            if st.battery_pct < c.battery_rtl_pct:
                battery, rtl = "rtl", True
                events.append({"kind": "battery_rtl", "level": "critical", "text": f"Battery {st.battery_pct:.0f}%: return to launch"})
            elif st.battery_pct < c.battery_warn_pct:
                battery = "warn"
                if not self._battery_warned:
                    self._battery_warned = True
                    events.append({"kind": "battery_warn", "level": "warning", "text": f"Battery {st.battery_pct:.0f}%"})
            else:
                battery = "ok"
        status = SafetyStatus(geofence_ok=fence_ok, geofence_margin_m=round(margin, 2) if margin is not None else None,
                              altitude_ok=alt_ok, battery_state=battery, link_ok=st.link_ok, people_over=people_over,
                              clamps=clamps)
        return SafetyResult(forward, right, up, yaw_rate, status, events, rtl)
