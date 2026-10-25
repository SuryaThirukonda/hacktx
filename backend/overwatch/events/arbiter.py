"""Alert arbiter (Rescue Copilot §11): safety override -> freshness/TTL -> dedupe/cooldown -> rate limit ->
optional AI prioritisation of *informational* alerts (decision task D5).

Urgent alerts (critical closing, local LiDAR proximity, warnings, sensor faults) are delivered immediately and
never wait for a model. Informational candidates (callouts, zone entries, reacquisitions) are held for at most the
decision deadline while D5 chooses which one, if any, to announce; on timeout/refusal the fixed-priority rules
decide and the alert is labelled accordingly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..config import AlertsCfg
from ..ids import IdGen
from ..schemas import Alert, DecisionRecord, Event, RelativeKinematics
from .narration import callout, noun

IMMEDIATE = {"CLOSING_FAST", "APPROACH", "LIDAR_PROXIMITY", "TRACK_LOST", "SOURCE_OFFLINE", "CALIBRATION_LOST",
             "LOCK_HOLD", "SAFETY", "POSE_DEGRADED", "AI_PAUSED", "LOCK_UNCERTAIN", "CROWD_NOTICE", "RESPONDER_LOST"}
INFORMATIONAL = {"POSITION_CALLOUT", "ZONE_ENTRY", "REACQUIRED"}
PRIORITY = {"CLOSING_FAST": 0, "LIDAR_PROXIMITY": 1, "APPROACH": 2, "TRACK_LOST": 3, "SOURCE_OFFLINE": 4,
            "CALIBRATION_LOST": 5, "REACQUIRED": 6, "ZONE_ENTRY": 7, "POSITION_CALLOUT": 8}


def _scripted(ev: Event) -> str:
    """Label suffix for alerts that came from a scripted conflict (scenario S16), not from observations."""
    return f" · scripted conflict {ev.data.get('scripted_conflict')}" if ev.source == "scripted" else ""


@dataclass
class _Pending:
    event: Event
    alert: Alert
    created_ms: float


class AlertArbiter:
    def __init__(self, cfg: AlertsCfg, profile: str, deadline_ms: float) -> None:
        self.cfg = cfg
        self.profile = profile
        self.deadline_ms = deadline_ms
        self.ids = IdGen("a")
        self.reset()

    def reset(self) -> None:
        self.pending: list[_Pending] = []
        self.d5_in_flight = False
        self.active: dict[str, Alert] = {}
        self.history: deque[Alert] = deque(maxlen=300)
        self.speech_times: deque[float] = deque(maxlen=200)
        self.last_key: dict[tuple[str, str | None], float] = {}
        self.suppressed: deque[dict] = deque(maxlen=100)

    # ---------------------------------------------------------------------------------------------------------
    def ingest(self, events: list[Event], kin: RelativeKinematics, now_ms: float) -> tuple[list[Alert], list[Event]]:
        """Returns (alerts delivered now, informational events waiting for a D5 decision)."""
        delivered: list[Alert] = []
        new_pending: list[Event] = []
        for ev in events:
            a = self._alert_for(ev, kin, now_ms)
            if a is None:
                continue
            key = (ev.kind, ev.track_id)
            if ev.severity not in ("critical", "local_proximity"):
                last = self.last_key.get(key)
                if last is not None and now_ms - last < self.cfg.cooldown_s * 1000.0 and ev.kind not in ("TRACK_LOST",):
                    self.suppressed.append({"event_id": ev.event_id, "kind": ev.kind, "reason": "cooldown"})
                    continue
            self.last_key[key] = now_ms
            if ev.kind in INFORMATIONAL:
                self.pending.append(_Pending(ev, a, now_ms))
                new_pending.append(ev)
            else:
                delivered.append(self._deliver(a, now_ms))
        return delivered, new_pending

    def flush_expired(self, now_ms: float) -> list[Alert]:
        """Pending informational alerts whose D5 decision did not arrive in time: rules choose (top priority)."""
        stale = [p for p in self.pending if now_ms - p.created_ms > self.deadline_ms + 150.0]
        if not stale:
            return []
        self.pending = [p for p in self.pending if p not in stale]
        best = min(stale, key=lambda p: PRIORITY.get(p.event.kind, 9))
        best.alert.message_source = f"rules_fallback (decision deadline missed){_scripted(best.event)}"
        for p in stale:
            if p is not best:
                self.suppressed.append({"event_id": p.event.event_id, "kind": p.event.kind, "reason": "lower priority (rules)"})
        return [self._deliver(best.alert, now_ms)]

    def pending_events(self) -> list[Event]:
        return [p.event for p in self.pending]

    def on_decision(self, rec: DecisionRecord, now_ms: float) -> list[Alert]:
        self.d5_in_flight = False
        ids = set(rec.event_ids)
        batch = [p for p in self.pending if p.event.event_id in ids]
        if not batch:
            return []
        self.pending = [p for p in self.pending if p not in batch]
        fresh = [p for p in batch if now_ms - p.created_ms <= p.event.ttl_ms]
        if rec.status in ("success",) and rec.acted:
            if rec.selected == "no_alert":
                for p in batch:
                    self.suppressed.append({"event_id": p.event.event_id, "kind": p.event.kind,
                                            "reason": f"{rec.engine}: no_alert", "decision_id": rec.decision_id})
                return []
            chosen = next((p for p in fresh if p.event.event_id == rec.selected), None)
            if chosen is None:
                # model chose an event that is no longer valid -> reject decision, rules decide
                rec.reason += " | selected event invalid/expired: rules fallback"
            else:
                chosen.alert.message_source = (f"{rec.engine}{_scripted(chosen.event)}" if chosen.event.source == "scripted"
                                               else f"{rec.engine}_with_validated_geometry")
                chosen.alert.decision_id = rec.decision_id
                for p in batch:
                    if p is not chosen:
                        self.suppressed.append({"event_id": p.event.event_id, "kind": p.event.kind,
                                                "reason": f"{rec.engine} chose {rec.selected}", "decision_id": rec.decision_id})
                return [self._deliver(chosen.alert, now_ms)]
        if not fresh:
            return []
        best = min(fresh, key=lambda p: PRIORITY.get(p.event.kind, 9))
        why = rec.status if rec.status != "success" else "low confidence"
        best.alert.message_source = f"rules_fallback ({why}){_scripted(best.event)}"
        best.alert.decision_id = rec.decision_id
        return [self._deliver(best.alert, now_ms)]

    # ---------------------------------------------------------------------------------------------------------
    def _deliver(self, a: Alert, now_ms: float) -> Alert:
        urgent = a.severity in ("critical", "local_proximity", "warning")
        if "phone_speech" in a.delivery and not urgent:
            recent = [t for t in self.speech_times if now_ms - t < 60000.0]
            if len(recent) >= self.cfg.max_speech_per_minute:
                a.delivery = [d for d in a.delivery if d != "phone_speech"]
                a.text += " (speech rate-limited)"
        if "phone_speech" in a.delivery:
            self.speech_times.append(now_ms)
        a.ts_ms = now_ms
        self.active[a.alert_id] = a
        self.history.append(a)
        return a

    def ack(self, alert_id: str, now_ms: float) -> Alert | None:
        a = self.active.get(alert_id) or next((h for h in self.history if h.alert_id == alert_id), None)
        if a is None:
            return None
        a.acked = True
        a.acked_ts_ms = now_ms
        self.active.pop(alert_id, None)
        return a

    def tick(self, now_ms: float) -> None:
        for aid, a in list(self.active.items()):
            if now_ms > a.expires_ts_ms and not (a.ack_required and not a.acked and now_ms - a.ts_ms < 15000):
                self.active.pop(aid, None)

    def active_alerts(self) -> list[Alert]:
        order = {"critical": 0, "local_proximity": 1, "warning": 2, "sensor_fault": 3, "attention": 4, "info": 5}
        return sorted(self.active.values(), key=lambda a: (order.get(a.severity, 9), -a.ts_ms))

    # ---------------------------------------------------------------------------------------------------------
    def _alert_for(self, ev: Event, kin: RelativeKinematics, now_ms: float) -> Alert | None:
        n = noun(self.profile)
        phone = ["dashboard", "phone_speech", "phone_haptic"]
        ttl = self.cfg.alert_ttl_s * 1000.0
        d = ev.data
        kw: dict = dict(alert_id=self.ids(), kind=ev.kind, ts_ms=now_ms, expires_ts_ms=now_ms + ttl,
                        evidence_event_ids=[ev.event_id])
        if ev.kind in ("CLOSING_FAST", "APPROACH", "POSITION_CALLOUT"):
            text, spoken = callout(self.profile, kin, ev.kind) if kin.valid else (ev.text, ev.text)
            sev = ev.severity
            haptic = {"CLOSING_FAST": "CLOSING_FAST", "APPROACH": None, "POSITION_CALLOUT": None}[ev.kind]
            if ev.kind == "APPROACH" and kin.bearing_rel_deg is not None:
                haptic = "DIRECTION_LEFT" if kin.bearing_rel_deg < 0 else "DIRECTION_RIGHT"
            delivery = phone if ev.kind != "POSITION_CALLOUT" else ["dashboard", "phone_speech"]
            return Alert(**kw, severity=sev, text=text, spoken_text=spoken, bearing_rel_deg=d.get("bearing_rel_deg"),
                         clock=d.get("clock"), range_m=d.get("range_m"), closing_mps=d.get("closing_mps"),
                         ttc_s=d.get("ttc_s"), haptic=haptic, delivery=delivery,
                         ack_required=ev.kind == "CLOSING_FAST",
                         can_read_as_confirmed_location=bool(ev.geometry_valid))
        if ev.kind == "TRACK_LOST":
            last = f"Last seen {kin.clock} o'clock, {kin.range_m:.0f} meters." if (kin.clock and kin.range_m) else "Last position shown on map."
            return Alert(**kw, severity="attention", text=f"{n} lost. {last}", spoken_text=f"{n} lost. {last}",
                         haptic="TARGET_LOST", delivery=phone)
        if ev.kind == "REACQUIRED":
            unv = "" if d.get("verified", True) else ", unverified"
            return Alert(**kw, severity="info", text=ev.text, spoken_text=f"{n} reacquired{unv}", haptic="REACQUIRED",
                         delivery=phone)
        if ev.kind == "LIDAR_PROXIMITY":
            dist = d.get("nearest_m")
            side = (d.get("sector") or "front").replace("_", " ")
            spoken = f"Obstacle {'ahead' if side == 'front' else side}, {dist:.1f} meters" if dist else "Obstacle ahead"
            return Alert(**kw, severity="local_proximity", text=ev.text, spoken_text=spoken, haptic="PROXIMITY",
                         delivery=phone, range_m=dist, message_source="local_lidar_rule")
        if ev.kind == "ZONE_ENTRY":
            return Alert(**kw, severity=ev.severity, text=ev.text, spoken_text=ev.text, delivery=["dashboard", "phone_speech"])
        if ev.kind in ("SOURCE_OFFLINE", "CALIBRATION_LOST", "POSE_DEGRADED"):
            spoken = {"SOURCE_OFFLINE": f"{ev.text.split(' ')[0]} {ev.text.split(' ')[1]} lost",
                      "CALIBRATION_LOST": "Drone positions unavailable",
                      "POSE_DEGRADED": "Phone tracking degraded"}[ev.kind]
            return Alert(**kw, severity="sensor_fault", text=ev.text, spoken_text=spoken, delivery=["dashboard", "phone_speech"])
        if ev.kind == "RESPONDER_LOST":
            return Alert(**kw, severity="attention", text=ev.text, spoken_text="", delivery=["dashboard"])
        if ev.kind in ("LOCK_UNCERTAIN", "LOCK_HOLD") and ev.role == "responder":
            return Alert(**kw, severity="attention", text=ev.text, spoken_text="", delivery=["dashboard"])
        if ev.kind in ("LOCK_UNCERTAIN", "LOCK_HOLD", "CROWD_NOTICE"):
            spoken = {"LOCK_UNCERTAIN": f"{n} identity uncertain", "LOCK_HOLD": f"{n} search stopped",
                      "CROWD_NOTICE": "Crowd scene, tracking limited"}[ev.kind]
            return Alert(**kw, severity="attention", text=ev.text, spoken_text=spoken, delivery=["dashboard", "phone_speech"])
        if ev.kind == "AI_PAUSED":
            return Alert(**kw, severity="sensor_fault", text=ev.text, spoken_text="", delivery=["dashboard"])
        if ev.kind == "SAFETY":
            crit = ev.severity == "critical"
            return Alert(**kw, severity=ev.severity, text=ev.text, spoken_text=ev.text if crit else "",
                         delivery=["dashboard", "phone_speech"] if crit else ["dashboard"])
        return None
