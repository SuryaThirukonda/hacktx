"""Deterministic event rules (Overwatch §5.4 step 6 + Rescue Copilot §11). Facts in, events out — no model calls.

Every event carries the measurements that triggered it, evidence references (frame, calibration, track) and
whether metric geometry was valid. Hysteresis, sustain timers and cooldowns prevent flapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import AlertsCfg, PhoneCfg
from ..ids import IdGen
from ..schemas import CalibrationStatus, Event, PhoneSummary, RelativeKinematics, Zone
from ..tracking.lock import LockEvent, short
from .narration import callout, noun


def point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


@dataclass
class RuleInput:
    now_ms: float
    profile: str
    kin: RelativeKinematics
    subject_state: str
    subject_track: str | None
    lock_events: list[LockEvent]
    tracks_world: list[tuple[str, tuple[float, float], str | None]]  # (track_id, xy, role)
    zones: list[Zone]
    phone: PhoneSummary
    source_status: dict[str, str]
    calibration: CalibrationStatus
    ai_paused: bool = False
    ai_reason: str | None = None
    safety_events: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    crowd: bool = False


class RuleEngine:
    def __init__(self, cfg: AlertsCfg, phone_cfg: PhoneCfg) -> None:
        self.cfg = cfg
        self.phone_cfg = phone_cfg
        self.ids = IdGen("e")
        self.reset()

    def reset(self) -> None:
        self._closing_since: float | None = None
        self._closing_episode = False
        self._closing_clear_since: float | None = None
        self._approach_episode = False
        self._approach_since: float | None = None
        self._last_callout_ms = -1e12
        self._last_callout_bearing: float | None = None
        self._zone_inside: dict[tuple[str, str], bool] = {}
        self._zone_change_ms: dict[tuple[str, str], float] = {}
        self._prev_status: dict[str, str] = {}
        self._prev_calib_valid: bool | None = None
        self._prev_lidar = False
        self._last_lidar_ms = -1e12
        self._prev_ai_paused = False
        self._prev_pose: str | None = None
        self._prev_crowd = False
        self._cooldown: dict[tuple[str, str | None], float] = {}

    def _event(self, kind: str, severity: str, now: float, text: str, inp: RuleInput, track: str | None = None,
               role: str | None = None, data: dict | None = None, geometry_valid: bool = False, ttl_ms: float = 2000.0) -> Event:
        return Event(event_id=self.ids(), kind=kind, severity=severity, ts_ms=now, text=text, track_id=track, role=role,
                     data=data or {}, evidence_refs=list(inp.evidence), geometry_valid=geometry_valid, ttl_ms=ttl_ms)

    def _cool(self, kind: str, key: str | None, now: float, cooldown_s: float | None = None) -> bool:
        cd = (cooldown_s if cooldown_s is not None else self.cfg.cooldown_s) * 1000.0
        last = self._cooldown.get((kind, key))
        if last is not None and now - last < cd:
            return False
        self._cooldown[(kind, key)] = now
        return True

    # ---------------------------------------------------------------------------------------------------------
    def evaluate(self, inp: RuleInput) -> list[Event]:
        c = self.cfg
        now = inp.now_ms
        out: list[Event] = []
        k = inp.kin
        subj = noun(inp.profile)

        # --- lock lifecycle events (from the lock layer) -----------------------------------------------------
        for le in inp.lock_events:
            if le.kind == "TRACK_LOST" and le.role == "subject":
                out.append(self._event("TRACK_LOST", "attention", now, le.text, inp, le.track_id, le.role, le.data))
            elif le.kind == "REACQUIRED" and le.role == "subject":
                out.append(self._event("REACQUIRED", "info", now, le.text, inp, le.track_id, le.role, le.data))
            elif le.kind in ("LOCK_UNCERTAIN", "LOCK_HOLD"):
                out.append(self._event(le.kind, "attention", now, le.text, inp, le.track_id, le.role, le.data))
            elif le.kind in ("LOCK_ENGAGED", "LOCK_RELEASED", "LOCK_HANDOVER"):
                out.append(self._event(le.kind, "info", now, le.text, inp, le.track_id, le.role, le.data, ttl_ms=1000))
            elif le.kind == "TRACK_LOST" and le.role == "responder":
                out.append(self._event("RESPONDER_LOST", "attention", now, le.text, inp, le.track_id, le.role, le.data))

        # --- kinematic rules (subject-induced closing; geometry must be valid) -------------------------------
        if k.valid and k.closing_mps is not None and k.range_m is not None:
            fast = (k.closing_mps > c.closing_fast_mps and (k.alignment_cos or 0.0) > c.closing_alignment_cos)
            ttc_hit = k.ttc_s is not None and k.ttc_s < c.ttc_critical_s and k.closing_mps > 1.0 and k.range_m < 20.0
            if fast or ttc_hit:
                if self._closing_since is None:
                    self._closing_since = now
                sustained = (now - self._closing_since) / 1000.0 >= c.closing_sustain_s or ttc_hit
                if sustained and not self._closing_episode:
                    self._closing_episode = True
                    text, _ = callout(inp.profile, k, "CLOSING_FAST")
                    out.append(self._event("CLOSING_FAST", "critical", now, text, inp, inp.subject_track, "subject",
                                           self._kin_data(k), True, ttl_ms=4000))
                self._closing_clear_since = None
            else:
                self._closing_since = None
                if self._closing_episode and k.closing_mps < 1.0:
                    if self._closing_clear_since is None:
                        self._closing_clear_since = now
                    elif now - self._closing_clear_since > 1000.0:
                        self._closing_episode = False
            approach = k.range_m < c.warning_range_m and k.closing_mps > c.warning_closing_mps
            if approach and not self._closing_episode:
                if self._approach_since is None:
                    self._approach_since = now
                if not self._approach_episode and now - self._approach_since >= 300.0:
                    self._approach_episode = True
                    text, _ = callout(inp.profile, k, "APPROACH")
                    out.append(self._event("APPROACH", "warning", now, text, inp, inp.subject_track, "subject",
                                           self._kin_data(k), True, ttl_ms=3000))
            elif not approach:
                self._approach_since = None
                if k.range_m > c.warning_range_m + 2.0 or k.closing_mps < 0.2:
                    self._approach_episode = False
            # periodic / bearing-change position callouts
            due = now - self._last_callout_ms >= c.callout_interval_s * 1000.0
            turned = (k.bearing_rel_deg is not None and self._last_callout_bearing is not None
                      and abs(((k.bearing_rel_deg - self._last_callout_bearing) + 180) % 360 - 180) > c.callout_bearing_change_deg
                      and now - self._last_callout_ms > 2500.0)
            if (due or turned) and inp.subject_state == "LOCKED":
                self._last_callout_ms = now
                self._last_callout_bearing = k.bearing_rel_deg
                text, _ = callout(inp.profile, k, "POSITION_CALLOUT")
                out.append(self._event("POSITION_CALLOUT", "info", now, text, inp, inp.subject_track, "subject",
                                       self._kin_data(k) | {"reason": "bearing change" if turned and not due else "interval"},
                                       True, ttl_ms=2500))
        else:
            self._closing_since = None
            self._approach_since = None

        # --- zones ---------------------------------------------------------------------------------------------
        if inp.calibration.valid:
            for tid, (x, y), role in inp.tracks_world:
                for z in inp.zones:
                    if z.kind != "task":
                        continue
                    key = (tid, z.zone_id)
                    inside = point_in_poly(x, y, z.polygon_xy_m)
                    prev = self._zone_inside.get(key)
                    if prev is None:
                        self._zone_inside[key] = inside
                        self._zone_change_ms[key] = now
                        continue
                    if inside != prev and now - self._zone_change_ms.get(key, 0) > 1000.0:
                        self._zone_inside[key] = inside
                        self._zone_change_ms[key] = now
                        if inside and role != "responder":
                            who = subj if role == "subject" else f"Person {short(tid)}"
                            sev = "attention" if role == "subject" else "info"
                            out.append(self._event("ZONE_ENTRY", sev, now, f"{who} entered {z.name}", inp, tid, role,
                                                   {"zone_id": z.zone_id, "zone": z.name, "xy": (round(x, 2), round(y, 2))},
                                                   True, ttl_ms=2500))

        # --- local LiDAR proximity (independent of cloud and of drone geometry) --------------------------------
        ph = inp.phone
        lidar_near = ph.connected and (ph.local_alert_active or (
            ph.nearest_m is not None and ph.nearest_m < self.phone_cfg.proximity_alert_m
            and ph.nearest_sector in ("front", "front_left", "front_right")))
        if lidar_near and not self._prev_lidar and now - self._last_lidar_ms > 2500.0:
            self._last_lidar_ms = now
            d = f"{ph.nearest_m:.1f} m" if ph.nearest_m is not None else "close"
            side = (ph.nearest_sector or "front").replace("_", " ")
            out.append(self._event("LIDAR_PROXIMITY", "local_proximity", now, f"Obstacle {side}, {d} (LiDAR)", inp,
                                   None, "responder", {"nearest_m": ph.nearest_m, "sector": ph.nearest_sector},
                                   False, ttl_ms=2500))
        self._prev_lidar = bool(lidar_near)

        # --- sensor health -------------------------------------------------------------------------------------
        for src, status in inp.source_status.items():
            prev = self._prev_status.get(src)
            self._prev_status[src] = status
            if prev is None or prev == status:
                continue
            bad = status in ("OFFLINE", "STALE")
            was_bad = prev in ("OFFLINE", "STALE")
            label = {"drone": "Drone video", "phone": "Responder phone", "telemetry": "Drone telemetry"}.get(src, src)
            if bad and not was_bad:
                out.append(self._event("SOURCE_OFFLINE", "sensor_fault", now, f"{label} {status.lower()}: claims from it suspended",
                                       inp, None, None, {"source": src, "status": status}, False, ttl_ms=5000))
            elif was_bad and not bad:
                out.append(self._event("SOURCE_RESTORED", "info", now, f"{label} restored", inp, None, None,
                                       {"source": src, "status": status}, False, ttl_ms=2000))
        cv = inp.calibration.valid
        if self._prev_calib_valid is not None and cv != self._prev_calib_valid:
            if not cv:
                out.append(self._event("CALIBRATION_LOST", "sensor_fault", now,
                                       f"Calibration lost ({inp.calibration.reason}): metric positions and speeds hidden",
                                       inp, None, None, {"reason": inp.calibration.reason}, False, ttl_ms=5000))
            else:
                out.append(self._event("CALIBRATION_RESTORED", "info", now,
                                       f"Calibration restored ({inp.calibration.source}, ±{inp.calibration.error_bound_m} m)",
                                       inp, None, None, {"source": inp.calibration.source}, True, ttl_ms=2000))
        self._prev_calib_valid = cv
        pose = ph.tracking_state if ph.connected else None
        if pose != self._prev_pose and self._prev_pose is not None and pose in ("limited", "not_available"):
            out.append(self._event("POSE_DEGRADED", "sensor_fault", now, "Responder phone pose degraded: map position held",
                                   inp, None, "responder", {"tracking_state": pose}, False, ttl_ms=4000))
        self._prev_pose = pose
        if inp.ai_paused != self._prev_ai_paused:
            if inp.ai_paused:
                out.append(self._event("AI_PAUSED", "sensor_fault", now, f"AI paused, rules active ({inp.ai_reason})", inp,
                                       data={"reason": inp.ai_reason}, ttl_ms=4000))
            else:
                out.append(self._event("AI_RESUMED", "info", now, "AI decisions resumed", inp, ttl_ms=2000))
        self._prev_ai_paused = inp.ai_paused
        if inp.crowd != self._prev_crowd:
            if inp.crowd:
                out.append(self._event("CROWD_NOTICE", "attention", now,
                                       "Crowd scene: subject locks disabled (not supported by design)", inp, ttl_ms=5000))
            self._prev_crowd = inp.crowd
        for se in inp.safety_events:
            if self._cool("SAFETY", se.get("kind"), now, 3.0):
                out.append(self._event("SAFETY", "attention" if se.get("level") != "critical" else "critical", now,
                                       se.get("text", "safety"), inp, data=se, ttl_ms=4000))
        return out

    @staticmethod
    def _kin_data(k: RelativeKinematics) -> dict:
        return {"range_m": k.range_m, "range_error_m": k.range_error_m, "bearing_rel_deg": k.bearing_rel_deg,
                "clock": k.clock, "closing_mps": k.closing_mps, "ttc_s": k.ttc_s, "alignment_cos": k.alignment_cos,
                "subject_speed_mps": k.subject_speed_mps}
