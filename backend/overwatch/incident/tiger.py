"""Tiger Data (TimescaleDB) mirror of the incident's time series.

SQLite stays the system of record (the append-only log that replays an incident). When ``TIGER_DATABASE_URL`` is
set, three streams are also written to hypertables in Tiger Cloud, in batches from a background thread that never
blocks the pipeline:

* ``ow_events``: every entry of the incident log (kind and payload), as it is appended;
* ``ow_telemetry``: the drone's position, speed, altitude, battery and the autonomy state, at the state rate;
* ``ow_detections``: every confirmed track's position and confidence, a few times a second.

Continuous aggregates (``ow_events_1m``, ``ow_telemetry_10s``, ``ow_detections_10s``) keep per-minute and per-10-s
rollups up to date, and the dashboard reads those for its live metrics; telemetry older than a day is compressed.
A lost connection is retried with backoff; while it is down the queue keeps the newest rows and drops the oldest.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    """CREATE TABLE IF NOT EXISTS ow_events (
        time timestamptz NOT NULL, incident_id text NOT NULL, seq bigint, kind text NOT NULL, payload jsonb)""",
    "SELECT create_hypertable('ow_events', 'time', if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS ow_events_incident ON ow_events (incident_id, time DESC)",
    """CREATE TABLE IF NOT EXISTS ow_telemetry (
        time timestamptz NOT NULL, incident_id text, session_id text, sim_t_ms double precision,
        lat double precision, lon double precision, alt_m double precision, speed_mps double precision,
        heading_deg double precision, battery_pct double precision, gimbal_deg double precision, mode text, fsm text)""",
    "SELECT create_hypertable('ow_telemetry', 'time', if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS ow_telemetry_incident ON ow_telemetry (incident_id, time DESC)",
    """CREATE TABLE IF NOT EXISTS ow_detections (
        time timestamptz NOT NULL, incident_id text, session_id text, track_id text, class text,
        confidence double precision, state text, role text, x_m double precision, y_m double precision, err_m double precision)""",
    "SELECT create_hypertable('ow_detections', 'time', if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS ow_detections_incident ON ow_detections (incident_id, time DESC)",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS ow_events_1m
       WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket('1 minute', time) AS bucket, incident_id, kind, count(*) AS n
       FROM ow_events GROUP BY bucket, incident_id, kind WITH NO DATA""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS ow_telemetry_10s
       WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket('10 seconds', time) AS bucket, incident_id, count(*) AS n, avg(alt_m) AS alt_m,
              avg(speed_mps) AS speed_mps, max(speed_mps) AS max_speed_mps, min(battery_pct) AS battery_pct
       FROM ow_telemetry GROUP BY bucket, incident_id WITH NO DATA""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS ow_detections_10s
       WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket('10 seconds', time) AS bucket, incident_id, count(*) AS n,
              count(DISTINCT track_id) AS tracks, avg(confidence) AS confidence
       FROM ow_detections GROUP BY bucket, incident_id WITH NO DATA""",
    "SELECT add_continuous_aggregate_policy('ow_events_1m', start_offset => INTERVAL '2 hours', end_offset => INTERVAL '30 seconds', schedule_interval => INTERVAL '30 seconds', if_not_exists => TRUE)",
    "SELECT add_continuous_aggregate_policy('ow_telemetry_10s', start_offset => INTERVAL '2 hours', end_offset => INTERVAL '10 seconds', schedule_interval => INTERVAL '30 seconds', if_not_exists => TRUE)",
    "SELECT add_continuous_aggregate_policy('ow_detections_10s', start_offset => INTERVAL '2 hours', end_offset => INTERVAL '10 seconds', schedule_interval => INTERVAL '30 seconds', if_not_exists => TRUE)",
    "ALTER TABLE ow_telemetry SET (timescaledb.compress, timescaledb.compress_segmentby = 'incident_id')",
    "SELECT add_compression_policy('ow_telemetry', INTERVAL '1 day', if_not_exists => TRUE)",
    "ALTER TABLE ow_detections SET (timescaledb.compress, timescaledb.compress_segmentby = 'incident_id')",
    "SELECT add_compression_policy('ow_detections', INTERVAL '1 day', if_not_exists => TRUE)",
]

INSERT = {
    "ow_events": "INSERT INTO ow_events (time, incident_id, seq, kind, payload) VALUES (%s, %s, %s, %s, %s)",
    "ow_telemetry": ("INSERT INTO ow_telemetry (time, incident_id, session_id, sim_t_ms, lat, lon, alt_m, speed_mps, "
                     "heading_deg, battery_pct, gimbal_deg, mode, fsm) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
    "ow_detections": ("INSERT INTO ow_detections (time, incident_id, session_id, track_id, class, confidence, state, role, "
                      "x_m, y_m, err_m) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
}
QUEUE_MAX = 20000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TigerMirror:
    def __init__(self, url: str | None, flush_s: float = 1.0) -> None:
        self.url = url
        self.enabled = bool(url)
        self.flush_s = flush_s
        self.queue: deque[tuple[str, tuple]] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: Any = None
        self.connected = False
        self.schema_ready = False
        self.rows_written = {"ow_events": 0, "ow_telemetry": 0, "ow_detections": 0}
        self.dropped = 0
        self.last_write_at: float | None = None
        self.last_error: str | None = None
        self._backoff = 1.0
        self.host = url.split("@")[-1].split("/")[0] if url else None  # never the credentials

    # ---------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tiger-mirror", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._close()

    def _put(self, table: str, row: tuple) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.queue.append((table, row))
            while len(self.queue) > QUEUE_MAX:
                self.queue.popleft()
                self.dropped += 1

    # -- the three streams ------------------------------------------------------------------------------------
    def event(self, ev: Any) -> None:
        """An incident-log entry (``incident.models.IncidentEvent``)."""
        at = ev.at if getattr(ev, "at", None) is not None else _now()
        self._put("ow_events", (at, ev.incident_id, int(getattr(ev, "seq", 0) or 0), ev.kind,
                                json.dumps(ev.payload, default=str)))

    def telemetry(self, incident_id: str | None, session_id: str, tel: Any, mode: str | None, fsm: str | None,
                  sim_t_ms: float | None) -> None:
        vel = getattr(tel, "vel_ned", None)
        speed = (vel[0] ** 2 + vel[1] ** 2) ** 0.5 if vel else None
        self._put("ow_telemetry", (_now(), incident_id, session_id, sim_t_ms, tel.lat, tel.lon, tel.alt_rel_m, speed,
                                   tel.yaw_deg, tel.battery_pct, tel.gimbal_pitch_deg, mode, fsm))

    def detections(self, incident_id: str | None, session_id: str, tracks: list[Any]) -> None:
        now = _now()
        for t in tracks:
            if t.state != "CONFIRMED" or t.box_xyxy_px is None:
                continue
            xy = t.ground_xy_m
            self._put("ow_detections", (now, incident_id, session_id, t.track_id, t.class_name, float(t.confidence),
                                        t.state, t.role, xy[0] if xy else None, xy[1] if xy else None, t.ground_error_m))

    # -- the writer thread -------------------------------------------------------------------------------------
    def _connect(self) -> bool:
        import psycopg

        try:
            self._conn = psycopg.connect(self.url, connect_timeout=10, autocommit=True)  # type: ignore[arg-type]
            if not self.schema_ready:
                with self._conn.cursor() as cur:
                    for stmt in SCHEMA:
                        try:
                            cur.execute(stmt)
                        except psycopg.Error as e:  # a policy that already exists, or a view that already has data
                            if "already" not in str(e).lower() and "exists" not in str(e).lower():
                                raise
                self.schema_ready = True
            self.connected = True
            self.last_error = None
            self._backoff = 1.0
            log.info("Tiger Data mirror connected to %s", self.host)
            return True
        except Exception as e:  # noqa: BLE001 - the mirror is best effort
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            self._close()
            return False

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None
        self.connected = False

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._conn is None and not self._connect():
                self._stop.wait(min(self._backoff, 60.0))
                self._backoff *= 2
                continue
            self._stop.wait(self.flush_s)
            self.flush()

    def flush(self) -> int:
        """Write everything queued, grouped by table. Returns the rows written."""
        with self._lock:
            batch = list(self.queue)
            self.queue.clear()
        if not batch or self._conn is None:
            if batch:
                with self._lock:
                    self.queue.extendleft(reversed(batch))
            return 0
        grouped: dict[str, list[tuple]] = {}
        for table, row in batch:
            grouped.setdefault(table, []).append(row)
        try:
            with self._conn.cursor() as cur:
                for table, rows in grouped.items():
                    cur.executemany(INSERT[table], rows)
                    self.rows_written[table] += len(rows)
            self.last_write_at = time.time()
            return len(batch)
        except Exception as e:  # noqa: BLE001 - reconnect next round, keep the rows
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("Tiger Data write failed (%s); reconnecting", self.last_error)
            self._close()
            with self._lock:
                self.queue.extendleft(reversed(batch))
                while len(self.queue) > QUEUE_MAX:
                    self.queue.popleft()
                    self.dropped += 1
            return 0

    # -- reads ------------------------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "connected": self.connected, "host": self.host, "schema_ready": self.schema_ready,
                "rows_written": dict(self.rows_written), "queued": len(self.queue), "dropped": self.dropped,
                "last_write_s_ago": round(time.time() - self.last_write_at, 1) if self.last_write_at else None,
                "last_error": self.last_error,
                "note": "a mirror: SQLite stays the system of record; hypertables with continuous aggregates"}

    def metrics(self, incident_id: str, minutes: int = 10) -> dict[str, Any]:
        """Live rollups from the continuous aggregates for one incident."""
        if self._conn is None:
            return {"available": False, "reason": self.last_error or "not connected"}
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT bucket, kind, n FROM ow_events_1m WHERE incident_id = %s AND bucket > now() - %s * INTERVAL '1 minute' "
                            "ORDER BY bucket", (incident_id, minutes))
                events = [{"bucket": b.isoformat(), "kind": k, "n": int(n)} for b, k, n in cur.fetchall()]
                cur.execute("SELECT bucket, n, alt_m, speed_mps, max_speed_mps, battery_pct FROM ow_telemetry_10s "
                            "WHERE incident_id = %s AND bucket > now() - %s * INTERVAL '1 minute' ORDER BY bucket", (incident_id, minutes))
                tel = [{"bucket": b.isoformat(), "n": int(n), "alt_m": _r(a), "speed_mps": _r(s), "max_speed_mps": _r(ms),
                        "battery_pct": _r(bp)} for b, n, a, s, ms, bp in cur.fetchall()]
                cur.execute("SELECT bucket, n, tracks, confidence FROM ow_detections_10s WHERE incident_id = %s AND "
                            "bucket > now() - %s * INTERVAL '1 minute' ORDER BY bucket", (incident_id, minutes))
                det = [{"bucket": b.isoformat(), "n": int(n), "tracks": int(t), "confidence": _r(c)} for b, n, t, c in cur.fetchall()]
                cur.execute("SELECT (SELECT count(*) FROM ow_events WHERE incident_id = %s), (SELECT count(*) FROM ow_telemetry WHERE incident_id = %s), "
                            "(SELECT count(*) FROM ow_detections WHERE incident_id = %s)", (incident_id, incident_id, incident_id))
                ne, nt, nd = cur.fetchone()
            return {"available": True, "incident_id": incident_id, "minutes": minutes, "events_1m": events, "telemetry_10s": tel,
                    "detections_10s": det, "totals": {"events": int(ne), "telemetry": int(nt), "detections": int(nd)},
                    "source": "TimescaleDB continuous aggregates (ow_events_1m, ow_telemetry_10s, ow_detections_10s)"}
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            self._close()
            return {"available": False, "reason": self.last_error}

    def delete_incident(self, incident_id: str) -> int:
        """Remove one incident's rows (tests and cleanup)."""
        if self._conn is None and not self._connect():
            return 0
        n = 0
        with self._conn.cursor() as cur:
            for table in ("ow_events", "ow_telemetry", "ow_detections"):
                cur.execute(f"DELETE FROM {table} WHERE incident_id = %s", (incident_id,))
                n += cur.rowcount
        return n


def _r(v: Any) -> float | None:
    return round(float(v), 2) if v is not None else None
