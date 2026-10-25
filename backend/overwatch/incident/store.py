"""SQLite persistence for incidents (stdlib sqlite3, WAL).

Raw observations are immutable rows; reviews live in their own table and the latest review wins when the two are
merged for display. Every change also appends to an event log with a monotonically increasing sequence number, which
is what the incident stream and replay read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from pydantic import BaseModel

from .models import (Alert, Incident, IncidentEvent, MissionAction, Observation, ObservationView, Review,
                     SceneAnchor, SensorSource, Track, Transform, utcnow)

T = TypeVar("T", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, created_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, incident_id TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations_raw (id TEXT PRIMARY KEY, incident_id TEXT, observed_at TEXT,
                                             body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, observation_id TEXT, at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY, incident_id TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS anchors (id TEXT PRIMARY KEY, incident_id TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transforms (id TEXT PRIMARY KEY, incident_id TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, incident_id TEXT, created_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, incident_id TEXT, created_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scene_assets (id TEXT PRIMARY KEY, incident_id TEXT, created_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS target_memory (id TEXT PRIMARY KEY, incident_id TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, at TEXT, kind TEXT,
                                   payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS obs_inc ON observations_raw(incident_id, observed_at);
CREATE INDEX IF NOT EXISTS rev_obs ON reviews(observation_id, at);
CREATE INDEX IF NOT EXISTS ev_inc ON events(incident_id, seq);
"""


class NotFound(KeyError):
    pass


class IncidentStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[IncidentEvent], None]] = []

    # -- plumbing ------------------------------------------------------------------------------------------------
    def close(self) -> None:
        self._db.close()

    def subscribe(self, fn: Callable[[IncidentEvent], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _q(self, sql: str, args: Iterable[Any] = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    def _put(self, table: str, cols: dict[str, Any], body: BaseModel) -> None:
        cols = {**cols, "body": body.model_dump_json()}
        names = ",".join(cols)
        marks = ",".join("?" for _ in cols)
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})", tuple(cols.values()))

    def _get(self, table: str, model: type[T], id_: str) -> T:
        rows = self._q(f"SELECT body FROM {table} WHERE id=?", (id_,))
        if not rows:
            raise NotFound(f"{table}:{id_}")
        return model.model_validate_json(rows[0][0])

    def _list(self, table: str, model: type[T], incident_id: str, order: str = "rowid") -> list[T]:
        return [model.model_validate_json(r[0])
                for r in self._q(f"SELECT body FROM {table} WHERE incident_id=? ORDER BY {order}", (incident_id,))]

    @staticmethod
    def _ts(d: datetime) -> str:
        return d.isoformat()

    def append_event(self, incident_id: str, kind: str, payload: dict[str, Any] | None = None,
                     at: datetime | None = None) -> IncidentEvent:
        ev = IncidentEvent(incident_id=incident_id, kind=kind, payload=payload or {}, at=at or utcnow())
        with self._lock:
            cur = self._db.execute("INSERT INTO events (incident_id, at, kind, payload) VALUES (?,?,?,?)",
                                   (incident_id, self._ts(ev.at), kind, json.dumps(ev.payload, default=str)))
            ev.seq = int(cur.lastrowid)
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:  # a broken listener must never block persistence
                pass
        return ev

    def _append_raw(self, ev: IncidentEvent) -> None:
        """Copy an event as-is (log replay); no listeners."""
        with self._lock:
            self._db.execute("INSERT INTO events (incident_id, at, kind, payload) VALUES (?,?,?,?)",
                             (ev.incident_id, self._ts(ev.at), ev.kind, json.dumps(ev.payload, default=str)))

    def events(self, incident_id: str, after_seq: int = 0, limit: int = 10_000) -> list[IncidentEvent]:
        rows = self._q("SELECT seq, at, kind, payload FROM events WHERE incident_id=? AND seq>? ORDER BY seq LIMIT ?",
                       (incident_id, after_seq, limit))
        return [IncidentEvent(seq=s, incident_id=incident_id, at=datetime.fromisoformat(a), kind=k,
                              payload=json.loads(p)) for s, a, k, p in rows]

    # -- incidents -----------------------------------------------------------------------------------------------
    def create_incident(self, inc: Incident) -> Incident:
        self._put("incidents", {"id": inc.id, "created_at": self._ts(inc.created_at)}, inc)
        self.append_event(inc.id, "incident_created", {"title": inc.title, "mission": inc.mission_mode,
                                                       "provenance": inc.provenance, "scenario": inc.scenario_id})
        return inc

    def get_incident(self, incident_id: str) -> Incident:
        return self._get("incidents", Incident, incident_id)

    def list_incidents(self) -> list[Incident]:
        return [Incident.model_validate_json(r[0]) for r in self._q("SELECT body FROM incidents ORDER BY created_at DESC")]

    def update_incident(self, inc: Incident, event: str | None = None, payload: dict[str, Any] | None = None) -> Incident:
        self._put("incidents", {"id": inc.id, "created_at": self._ts(inc.created_at)}, inc)
        if event:
            self.append_event(inc.id, event, payload or {})
        return inc

    def set_mission(self, incident_id: str, mission: str, by: str = "operator") -> Incident:
        inc = self.get_incident(incident_id)
        prev = inc.mission_mode
        inc.mission_mode = mission  # type: ignore[assignment]
        Incident.model_validate(inc.model_dump())  # re-validate the literal
        return self.update_incident(inc, "mission_changed", {"from": prev, "to": mission, "by": by})

    # -- sources -------------------------------------------------------------------------------------------------
    def upsert_source(self, incident_id: str, src: SensorSource, log: bool = True) -> SensorSource:
        self._put("sources", {"id": f"{incident_id}/{src.id}", "incident_id": incident_id}, src)
        if log:
            self.append_event(incident_id, "source_updated", src.model_dump(mode="json"))
        return src

    def list_sources(self, incident_id: str) -> list[SensorSource]:
        return self._list("sources", SensorSource, incident_id)

    # -- observations and reviews ----------------------------------------------------------------------------
    def add_observation(self, obs: Observation) -> Observation:
        self.get_incident(obs.incident_id)  # must exist
        self._put("observations_raw", {"id": obs.id, "incident_id": obs.incident_id,
                                       "observed_at": self._ts(obs.observed_at)}, obs)
        self.append_event(obs.incident_id, "observation", {"observation": obs.model_dump(mode="json")})
        return obs

    def get_observation(self, obs_id: str) -> Observation:
        return self._get("observations_raw", Observation, obs_id)

    def review(self, rev: Review) -> ObservationView:
        obs = self.get_observation(rev.observation_id)
        self._put("reviews", {"id": rev.id, "observation_id": rev.observation_id, "at": self._ts(rev.at)}, rev)
        self.append_event(obs.incident_id, "review", rev.model_dump(mode="json"))
        return self.view(obs)

    def latest_review(self, obs_id: str) -> Review | None:
        rows = self._q("SELECT body FROM reviews WHERE observation_id=? ORDER BY at DESC, rowid DESC LIMIT 1", (obs_id,))
        return Review.model_validate_json(rows[0][0]) if rows else None

    def view(self, obs: Observation, now: datetime | None = None) -> ObservationView:
        rev = self.latest_review(obs.id)
        return ObservationView(observation=obs, review_status=rev.status if rev else "pending",
                               reviewer=rev.reviewer if rev else None, review_timestamp=rev.at if rev else None,
                               stale=obs.is_stale(now))

    def list_observations(self, incident_id: str, status: str | None = None,
                          now: datetime | None = None) -> list[ObservationView]:
        views = [self.view(o, now) for o in self._list("observations_raw", Observation, incident_id, "observed_at")]
        return [v for v in views if status is None or v.review_status == status]

    # -- tracks, anchors, transforms -------------------------------------------------------------------------
    def upsert_track(self, trk: Track, event: str | None = None) -> Track:
        self._put("tracks", {"id": trk.id, "incident_id": trk.incident_id}, trk)
        if event:
            self.append_event(trk.incident_id, event, {"track_id": trk.id, "status": trk.tracking_status,
                                                       "identity": trk.identity_status,
                                                       "provenance": trk.provenance,
                                                       "first_seen": self._ts(trk.first_seen)})
        return trk

    def get_track(self, track_id: str) -> Track:
        return self._get("tracks", Track, track_id)

    def list_tracks(self, incident_id: str) -> list[Track]:
        return self._list("tracks", Track, incident_id)

    def add_anchor(self, anc: SceneAnchor) -> SceneAnchor:
        self._put("anchors", {"id": anc.id, "incident_id": anc.incident_id}, anc)
        self.append_event(anc.incident_id, "anchor", anc.model_dump(mode="json"))
        return anc

    def list_anchors(self, incident_id: str) -> list[SceneAnchor]:
        return self._list("anchors", SceneAnchor, incident_id)

    def add_transform(self, incident_id: str, tf: Transform) -> Transform:
        self._put("transforms", {"id": tf.id, "incident_id": incident_id}, tf)
        self.append_event(incident_id, "transform", tf.model_dump(mode="json"))
        return tf

    def list_transforms(self, incident_id: str) -> list[Transform]:
        return self._list("transforms", Transform, incident_id)

    def find_transform(self, incident_id: str, source: str, target: str) -> Transform | None:
        for tf in reversed(self.list_transforms(incident_id)):
            if tf.source_frame == source and tf.target_frame == target and tf.is_valid():
                return tf
        return None

    # -- alerts and actions ----------------------------------------------------------------------------------
    def create_alert(self, alert: Alert) -> Alert:
        self._put("alerts", {"id": alert.id, "incident_id": alert.incident_id,
                             "created_at": self._ts(alert.created_at)}, alert)
        self.append_event(alert.incident_id, "alert", alert.model_dump(mode="json"))
        return alert

    def get_alert(self, alert_id: str) -> Alert:
        return self._get("alerts", Alert, alert_id)

    def mark_delivered(self, alert_id: str) -> Alert:
        with self._lock:  # atomic read/update/log: retries from concurrent clients are idempotent
            a = self.get_alert(alert_id)
            if a.delivered_at is None:
                a.delivered_at = utcnow()
                if a.status == "pending":
                    a.status = "delivered"
                self._put("alerts", {"id": a.id, "incident_id": a.incident_id, "created_at": self._ts(a.created_at)}, a)
                self.append_event(a.incident_id, "alert_delivered", {"alert_id": a.id, "at": self._ts(a.delivered_at)},
                                  at=a.delivered_at)
            return a

    def acknowledge_alert(self, alert_id: str, by: str = "responder") -> Alert:
        with self._lock:  # atomic read/update/log: retries from concurrent clients are idempotent
            a = self.get_alert(alert_id)
            if a.acknowledged_at is None:
                a.acknowledged_at = utcnow()
                a.acknowledged_by = by
                a.status = "acknowledged"
                self._put("alerts", {"id": a.id, "incident_id": a.incident_id, "created_at": self._ts(a.created_at)}, a)
                self.append_event(a.incident_id, "alert_acknowledged",
                                  {"alert_id": a.id, "by": by, "at": self._ts(a.acknowledged_at)}, at=a.acknowledged_at)
            return a

    def list_alerts(self, incident_id: str) -> list[Alert]:
        return self._list("alerts", Alert, incident_id, "created_at")

    def add_action(self, act: MissionAction) -> MissionAction:
        self._put("actions", {"id": act.id, "incident_id": act.incident_id, "created_at": self._ts(act.created_at)}, act)
        self.append_event(act.incident_id, "decision_proposal", act.model_dump(mode="json"))
        return act

    def update_action(self, act: MissionAction, event: str) -> MissionAction:
        self._put("actions", {"id": act.id, "incident_id": act.incident_id, "created_at": self._ts(act.created_at)}, act)
        self.append_event(act.incident_id, event, {"action_id": act.id, "approval": act.approval_status,
                                                   "execution": act.execution_status})
        return act

    def get_action(self, action_id: str) -> MissionAction:
        return self._get("actions", MissionAction, action_id)

    def list_actions(self, incident_id: str) -> list[MissionAction]:
        return self._list("actions", MissionAction, incident_id, "created_at")

    def save_target(self, incident_id: str, target: dict[str, Any], event: str | None = None) -> None:
        """Latest read model plus append-only snapshots/transitions in the existing incident log."""
        if target.get("incident_id") != incident_id or not target.get("logical_subject_id"):
            raise ValueError("target memory must belong to its incident")
        self.get_incident(incident_id)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO target_memory (id, incident_id, body) VALUES (?,?,?)",
                             (target["logical_subject_id"], incident_id, json.dumps(target, allow_nan=False)))
        if event:
            self.append_event(incident_id, event, target)

    def list_targets(self, incident_id: str) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self._q("SELECT body FROM target_memory WHERE incident_id=? ORDER BY rowid",
                                               (incident_id,))]
