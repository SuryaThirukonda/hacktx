"""Tiger Data (TimescaleDB) mirror: hypertables, continuous aggregates and a round trip against the real service.
Skipped without TIGER_DATABASE_URL."""

import os
import time
import uuid

import pytest

from overwatch.config import load_config
from overwatch.incident.models import IncidentEvent
from overwatch.incident.tiger import TigerMirror
from overwatch.schemas import DroneTelemetry, Track

load_config()  # reads .env into the environment
pytestmark = pytest.mark.skipif(not os.environ.get("TIGER_DATABASE_URL"), reason="no Tiger Data service configured")


def test_mirror_round_trip_and_rollups():
    m = TigerMirror(os.environ["TIGER_DATABASE_URL"])
    if not m._connect():
        pytest.skip(f"Tiger Data service not reachable from here: {m.last_error}")
    iid = f"TEST-{uuid.uuid4().hex[:8]}"
    try:
        with m._conn.cursor() as cur:
            cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name LIKE 'ow_%'")
            tables = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT view_name FROM timescaledb_information.continuous_aggregates WHERE view_name LIKE 'ow_%'")
            views = {r[0] for r in cur.fetchall()}
        assert {"ow_events", "ow_telemetry", "ow_detections"} <= tables
        assert {"ow_events_1m", "ow_telemetry_10s", "ow_detections_10s"} <= views
        m.event(IncidentEvent(incident_id=iid, kind="observation", payload={"label": "possible person"}, seq=1))
        m.event(IncidentEvent(incident_id=iid, kind="review", payload={"status": "confirmed"}, seq=2))
        for i in range(5):
            m.telemetry(iid, "s-test", DroneTelemetry(capture_ts_ms=i * 100.0, lat=30.2669, lon=-97.7729, alt_rel_m=24.0 + i,
                                                     yaw_deg=90.0, vel_ned=(3.0, 4.0, 0.0), battery_pct=90.0 - i), "FOLLOW", "TRACKING", i * 100.0)
        m.detections(iid, "s-test", [Track(track_id="drone:1:e1", class_name="person", confidence=0.8, state="CONFIRMED",
                                           visibility="visible", model_id="sim-blob-v1", box_xyxy_px=(1, 2, 3, 4),
                                           first_seen_ts_ms=0, last_seen_ts_ms=0, hits=9, ground_xy_m=(1.0, 2.0), ground_error_m=1.5),
                                     Track(track_id="drone:2:e1", class_name="person", confidence=0.3, state="TENTATIVE",
                                           visibility="visible", model_id="sim-blob-v1", box_xyxy_px=(1, 2, 3, 4),
                                           first_seen_ts_ms=0, last_seen_ts_ms=0, hits=1)])
        n = m.flush()
        assert n == 8 and m.rows_written == {"ow_events": 2, "ow_telemetry": 5, "ow_detections": 1}  # the tentative track is not a detection
        time.sleep(0.5)
        r = m.metrics(iid, minutes=5)
        assert r["available"], r
        assert r["totals"] == {"events": 2, "telemetry": 5, "detections": 1}
        kinds = {e["kind"] for e in r["events_1m"]}
        assert kinds == {"observation", "review"}  # real-time aggregation: the rollup includes rows not yet materialised
        assert r["telemetry_10s"] and r["telemetry_10s"][-1]["n"] == 5 and r["telemetry_10s"][-1]["max_speed_mps"] == 5.0
        assert r["detections_10s"] and r["detections_10s"][-1]["tracks"] == 1
        st = m.status()
        assert st["connected"] and st["host"] and "@" not in st["host"] and ":" in st["host"]  # host only, never credentials
    finally:
        assert m.delete_incident(iid) == 8
        m.stop()
