"""Incident service: the persisted incident store bridged to the live pipeline.

* Creating an incident also starts the pipeline's in-memory incident (locks require one).
* Alerts and acknowledgments the pipeline emits are mirrored into the store with the same ids, so an alert can be
  acknowledged from either side (phone socket, console, or the incident API).
* Every store event fans out to per-incident subscriber queues for ``/ws/incidents/{id}`` (thread-safe).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..config import REPO_ROOT
from .models import (PROVENANCE_FROM_WIRE, Alert, Incident, IncidentEvent, SensorSource, utcnow)
from .store import IncidentStore, NotFound

if TYPE_CHECKING:
    from pathlib import Path

    from ..runtime import Runtime
    from .models import MissionAction, Observation

log = logging.getLogger(__name__)

SEVERITY_FROM_WIRE = {"info": "info", "attention": "attention", "warning": "warning", "critical": "critical",
                      "sensor_fault": "warning", "local_proximity": "critical"}


def default_db_path() -> str:
    return os.environ.get("OVERWATCH_DB") or str(REPO_ROOT / "data" / "overwatch.db")


class IncidentService:
    def __init__(self, rt: "Runtime", db_path: str | None = None) -> None:
        self.rt = rt
        self.store = IncidentStore(db_path or default_db_path())
        self.current_id: str | None = None
        self.responder: dict[str, dict[str, Any]] = {}  # incident_id -> latest responder position
        self._tracks: dict[str, Any] = {}  # session-namespaced pipeline track id -> incident Track
        self._source_state: dict[str, dict[str, str]] = {}
        self.follow: dict[str, Any] = {}  # incident -> missions.follow.FollowTracker
        self._follow_logged_ms: dict[str, float] = {}  # incident -> last logged state per source
        self._follow_engaged: dict[str, float] = {}  # incident -> engage time of the last subject lock adopted
        self.flagged_tracks: dict[str, set[str]] = {}  # incident -> tracks the Search mission already flagged
        self.proposed_openings: dict[str, set[str]] = {}  # incident -> fixture openings already proposed
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.store.subscribe(self._fanout)
        from .person_mark import PersonMarkService
        self.person_mark = PersonMarkService(self)
        self._restore()

    def _restore(self) -> None:
        """After a backend restart, continue the most recent active incident of the same data kind."""
        try:
            for inc in self.store.list_incidents():
                if inc.status != "active" or inc.provenance != self.wire_provenance():
                    continue
                self.current_id = inc.id
                self.rt.pipeline.start_incident(inc.id, consent=True)
                if inc.scenario_id and self.rt.sim is not None and self.rt.sim.scenario.id != inc.scenario_id:
                    try:
                        self.rt.control("restart", inc.scenario_id)
                    except (KeyError, ValueError):
                        pass
                self.store.append_event(inc.id, "backend_restarted", {"session_id": self.rt.session_id})
                return
        except Exception:  # noqa: BLE001  a damaged database must not stop the backend
            log.exception("could not restore the last incident")

    # -- streaming -----------------------------------------------------------------------------------------------
    def subscribe(self, incident_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._subs[incident_id].add(q)
        return q

    def unsubscribe(self, incident_id: str, q: asyncio.Queue) -> None:
        self._subs[incident_id].discard(q)

    def _fanout(self, ev: IncidentEvent) -> None:
        subs = list(self._subs.get(ev.incident_id, ()))
        if not subs:
            return
        loop = self.rt.loop
        msg = {"type": "incident_event", **ev.model_dump(mode="json")}

        def push() -> None:
            for q in subs:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(msg)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(push)
        else:
            push()

    # -- incidents -----------------------------------------------------------------------------------------------
    def wire_provenance(self) -> str:
        return {"SIMULATED": "simulated", "REPLAY": "replay", "LIVE": "live"}.get(self.rt.mode, "simulated")

    def create(self, title: str, mission: str = "search", scenario_id: str | None = None,
               notes: str = "") -> Incident:
        if scenario_id is None and self.rt.sim is not None:
            scenario_id = self.rt.sim.scenario.id
        if scenario_id and self.rt.sim is not None and self.rt.sim.scenario.id != scenario_id:
            self.rt.control("restart", scenario_id)  # validates the id; the simulator loads it next step
        inc = Incident(title=title, mission_mode=mission, scenario_id=scenario_id, operator_notes=notes,
                       provenance=self.wire_provenance())  # type: ignore[arg-type]
        geo = getattr(self.rt.cfg.world, "geo_reference", None) if hasattr(self.rt.cfg, "world") else None
        if geo is not None and inc.coordinate_reference is not None:
            # the configured reference is a stand-in until an operator sets a surveyed origin
            inc.coordinate_reference.origin_lat = float(geo.lat)
            inc.coordinate_reference.origin_lon = float(geo.lon)
            inc.coordinate_reference.origin_alt_m = float(getattr(geo, "alt_m", 0.0) or 0.0)
            inc.coordinate_reference.north_offset_deg = float(getattr(self.rt.cfg.world, "north_offset_deg", 0.0) or 0.0)
            inc.coordinate_reference.note = "configured reference (config/default.yaml), not surveyed"
        self.store.create_incident(inc)
        self.current_id = inc.id
        self.rt.pipeline.start_incident(inc.id, consent=True)
        self.store.upsert_source(inc.id, self.drone_source(), log=True)
        return inc

    def current(self) -> Incident | None:
        if self.current_id is None:
            return None
        try:
            return self.store.get_incident(self.current_id)
        except NotFound:
            return None

    def drone_source(self) -> SensorSource:
        """Status of the active drone-video source (never reports a live link that is not there)."""
        v = self.rt.video
        if v.active == "replay":
            return SensorSource(id="replay", kind="replay", name="Recorded session", source_mode="replay",
                                connection_status="connected", last_seen_at=utcnow(), detail="recorded session replay")
        return v.status(v.active)

    # -- pipeline bridge -----------------------------------------------------------------------------------------
    def on_outbox(self, msg: dict[str, Any]) -> None:
        """Called from the runtime publish loop for every pipeline outbox message."""
        if self.current_id is None:
            return
        t = msg.get("type")
        try:
            if t == "alert":
                aid = self.store_alert_id(msg["alert_id"])
                if self._exists_alert(aid):
                    return
                a = Alert(id=aid, incident_id=self.current_id,
                          severity=SEVERITY_FROM_WIRE.get(msg.get("severity", "info"), "info"),  # type: ignore[arg-type]
                          message=msg.get("text", ""), provenance=self.wire_provenance(),  # type: ignore[arg-type]
                          status="delivered", delivered_at=utcnow())
                self.store.create_alert(a)
            elif t == "ack" and msg.get("alert_id") and self._exists_alert(self.store_alert_id(msg["alert_id"])):
                self.store.acknowledge_alert(self.store_alert_id(msg["alert_id"]), by="responder")
            elif t == "event":
                self.store.append_event(self.current_id, "pipeline_event",
                                        {k: msg.get(k) for k in ("event_id", "kind", "role", "severity", "text")})
            elif t == "memory":  # object memory: new, hidden, reattached, forgotten (state changes only)
                self.store.append_event(self.current_id, msg["kind"],
                                        {k: msg.get(k) for k in ("object_id", "label", "state", "xy", "track_id", "link",
                                                                 "previous_track_id", "hidden_s", "was", "behind_m", "why")
                                         if msg.get(k) is not None})
            elif t == "decision":
                self.store.append_event(self.current_id, "decision",
                                        {k: msg.get(k) for k in ("decision_id", "task", "engine", "status", "selected",
                                                                 "acted", "reason", "shadow", "latency_ms")}
                                        | {"probabilities": next((a.get("probabilities") for a in msg.get("answers") or []
                                                                  if a.get("type") == "choice"), None),
                                           "rules_baseline": (msg.get("facts") or {}).get("_rules_baseline")})
            elif t in ("pursuit", "voice"):  # belief and voice-command events (their payloads carry provenance)
                self.store.append_event(self.current_id, msg["kind"], {k: v for k, v in msg.items() if k not in ("type", "kind")})
        except Exception:  # noqa: BLE001  mirroring must never break the publish loop
            log.exception("incident mirror failed for %s", t)

    def _log_source_status(self, incident_id: str) -> None:
        """Sensor connections: log every change of a source's state (OK, STALE, OFFLINE, NOT_CONFIGURED)."""
        p = self.rt.pipeline
        with p.lock:
            now_status = p._source_status(self.rt.clock.now_ms())  # noqa: SLF001 - the pipeline's own health view
        last = self._source_state.setdefault(incident_id, {})
        for src, state in now_status.items():
            if last.get(src) != state:
                self.store.append_event(incident_id, "sensor_status", {"source": src, "from": last.get(src), "to": state})
                last[src] = state

    def sim_truth(self, box: tuple[float, float, float, float] | None) -> dict[str, Any] | None:
        """Simulator ground truth for an image box (evaluation only): the actor whose rendered box overlaps most."""
        sim = self.rt.sim
        if sim is None or box is None or self.wire_provenance() != "simulated":
            return None
        from ..evaluation.gt_eval import _iou as iou

        best, best_iou = None, 0.3
        for aid, g in (sim.last_gt.get("actors") or {}).items():
            b = g.get("box_xyxy")
            if not b or g.get("visibility", 0) <= 0.3:
                continue
            v = iou(tuple(box), tuple(b))
            if v > best_iou:
                best, best_iou = aid, v
        a = sim.actor(best) if best else None
        if a is None:
            return None
        return {"actor_id": a.id, "xy": [round(float(a.pos[0]), 3), round(float(a.pos[1]), 3)], "iou": round(best_iou, 3),
                "source": "simulator ground truth (evaluation only)"}

    # -- track history ----------------------------------------------------------------------------------
    def sync_tracks(self) -> None:
        """Persist person tracks (detection history) and their transitions; called about once a second.

        Only tracks that were CONFIRMED at least once are kept. Identity stays "unverified" unless an operator
        designated the track (Follow). Positions are stored only when the track has a calibrated ground position.
        """
        if self.current_id is None:
            return
        from .models import BoundingBox, Position, Track, TrackPoint

        inc_id = self.current_id
        p = self.rt.pipeline
        now = utcnow()
        snap = {t.track_id: t for t in p.tracks_snapshot(self.rt.clock.now_ms()) if t.class_name == "person"}
        prov = PROVENANCE_FROM_WIRE.get(p.counters["drone"].provenance or "", self.wire_provenance())
        subj = p.locks["subject"].track_id if p.locks["subject"].active else None
        inc = self.current()
        if inc is None:
            return
        seen = set()
        for wid, t in snap.items():
            sid = f"{self.rt.session_id}.{wid}"
            seen.add(sid)
            known = self._tracks.get(sid)
            if known is None and t.state != "CONFIRMED":
                continue
            if known is None:
                known = Track(id=sid, incident_id=inc_id, first_seen=now, last_seen=now, provenance=prov)  # type: ignore[arg-type]
                self._tracks[sid] = known
                self.store.upsert_track(known, "track_started")
            status = "lost" if t.state == "LOST" or t.visibility == "lost" else "active"
            if t.state != "LOST":
                known.last_seen = now
                pos = Position(frame=inc.enu_frame, x=float(t.ground_xy_m[0]), y=float(t.ground_xy_m[1])) \
                    if t.ground_xy_m is not None and t.ground_error_m is not None else None
                box = BoundingBox(frame="image:drone", x0=t.box_xyxy_px[0], y0=t.box_xyxy_px[1], x1=t.box_xyxy_px[2],
                                  y1=t.box_xyxy_px[3]) if t.box_xyxy_px else None
                known.positions = (known.positions + [TrackPoint(t=now, position=pos, bbox=box)])[-200:]
            designated = subj == wid
            if designated and known.identity_status != "operator_designated":
                known.identity_status = "operator_designated"
                known.designated_by = "operator"
                self.store.upsert_track(known, "track_designated")
            if status != known.tracking_status:
                known.tracking_status = status  # type: ignore[assignment]
                self.store.upsert_track(known, "track_lost" if status == "lost" else "track_reacquired")
            else:
                self.store.upsert_track(known)
        self._log_source_status(inc_id)
        for sid, known in list(self._tracks.items()):
            if sid not in seen and known.tracking_status != "ended":
                known.tracking_status = "ended"
                self.store.upsert_track(known, "track_ended")
                del self._tracks[sid]

    def run_missions(self) -> None:
        """Mission-specific automation for the current incident (about once a second)."""
        inc = self.current()
        if inc is None:
            return
        from ..missions import search, structure

        structure.register_buildings(self, inc.id)
        if inc.mission_mode == "search":
            search.auto_flag(self, inc.id)
        elif inc.mission_mode == "structure":
            structure.simulate_openings(self, inc.id)
        if inc.id in self.follow:
            self.follow_update(inc.id)
        from ..decisions import recommendations

        recommendations.deterministic(self.store, self.store.get_incident(inc.id), bool(self.rt.pipeline.calib.valid))

    # -- follow ----------------------------------------------------------------------------------------------
    def follow_tracker(self, incident_id: str):
        from ..missions.follow import FollowTracker

        if incident_id not in self.follow:
            self.follow[incident_id] = FollowTracker(lambda kind, payload: self.store.append_event(incident_id, kind, payload))
        return self.follow[incident_id]

    def follow_update(self, incident_id: str) -> None:
        p = self.rt.pipeline
        now = self.rt.clock.now_ms()
        with p.lock:
            lock = p.locks["subject"].snapshot(now)
            raw = p.locks["subject"]
            engaged = (raw.since_ms, raw.locked_by) if raw.state != "UNLOCKED" else None
        tracks = {t.track_id: {"box": t.box_xyxy_px, "visible": t.state != "LOST" and t.visibility != "lost",
                               "xy": tuple(t.ground_xy_m) if t.ground_xy_m is not None and t.ground_error_m is not None else None,
                               "err": t.ground_error_m if t.ground_xy_m is not None else None}
                  for t in p.tracks_snapshot(now)}
        ft = self.follow_tracker(incident_id)
        # a person can lock the subject outside this panel (the ops console, the scripted demo operator): the lock's
        # engage time changes only then, never on an automatic re-bind, so that lock becomes the designation
        if engaged and engaged[0] is not None and lock.track_id and engaged[0] != self._follow_engaged.get(incident_id):
            self._follow_engaged[incident_id] = engaged[0]
            if ft.s.designation == 0 or ft.s.status == "released" or ft.s.track_id != lock.track_id:
                ft.designate(lock.track_id, engaged[1] or "operator", now)
        ft.update(lock, tracks, now)
        last = ft.s.trail[-1] if ft.s.trail else None
        if last is not None and last.t_ms - self._follow_logged_ms.get(incident_id, -1e12) >= 1000.0:
            self._follow_logged_ms[incident_id] = last.t_ms
            self.store.append_event(incident_id, "follow_point", {"t_ms": last.t_ms, "track_id": last.track_id,
                                                                  "xy": last.xy, "err_m": last.err_m,
                                                                  "identity": last.identity})

    def follow_designate(self, incident_id: str, track_id: str, by: str = "operator") -> dict[str, Any]:
        p = self.rt.pipeline
        p.lock_track("subject", track_id, by)
        ft = self.follow_tracker(incident_id)
        ft.designate(track_id, by, self.rt.clock.now_ms())
        return ft.view(self.rt.clock.now_ms())

    def follow_confirm(self, incident_id: str, by: str = "operator") -> dict[str, Any]:
        ft = self.follow_tracker(incident_id)
        tid = ft.s.track_id
        ft.confirm_binding(by, self.rt.clock.now_ms())
        try:
            self.rt.pipeline.confirm_candidate(tid)  # the lock is marked verified too
        except KeyError:
            pass
        return ft.view(self.rt.clock.now_ms())

    def follow_reject(self, incident_id: str, by: str = "operator") -> dict[str, Any]:
        """'Not them': the unverified re-binding is the wrong person; the lock searches again and the belief drops
        that person's hypothesis (pipeline.reject_rebind logs it)."""
        ft = self.follow_tracker(incident_id)
        self.rt.pipeline.reject_rebind(by)
        return ft.view(self.rt.clock.now_ms())

    def follow_release(self, incident_id: str, by: str = "operator") -> dict[str, Any]:
        ft = self.follow_tracker(incident_id)
        self.rt.pipeline.unlock("subject")
        ft.release(by, self.rt.clock.now_ms())
        return ft.view(self.rt.clock.now_ms())

    # -- recommendations -------------------------------------------------------------------------------------
    def brief_text(self, incident_id: str, observation_id: str) -> str | None:
        from .briefing import responder_targets

        t = responder_targets(self.store, incident_id, self.responder.get(incident_id))
        return next((x["text"] for x in t["targets"] if x["observation_id"] == observation_id), None)

    def propose(self, incident_id: str, kind: str, params: dict[str, Any], proposed_by: str,
                engine: str | None = None, rationale: str | None = None) -> "MissionAction":
        from ..decisions import recommendations

        inc = self.store.get_incident(incident_id)
        brief = self.brief_text(incident_id, params["observation_id"]) if kind == "notify_responder" and \
            params.get("observation_id") else None
        return recommendations.propose(self.store, inc, kind, params, proposed_by, engine=engine, rationale=rationale,
                                       brief=brief)

    def decide_action(self, action_id: str, approve: bool, by: str = "operator") -> "MissionAction":
        """A person approves or rejects. Approving notify_responder re-checks the data and sends the templated brief."""
        from ..decisions.recommendations import ProposalRejected
        from .models import Alert

        act = self.store.get_action(action_id)
        if act.approval_status != "pending":
            raise ValueError(f"action is {act.approval_status}, not pending")
        if not approve:
            act.approval_status = "rejected"
            act.execution_status = "cancelled"
            act.parameters["decided_by"] = by
            return self.store.update_action(act, "action_rejected")
        if act.action_type == "notify_responder":
            oid = act.parameters["observation_id"]
            v = self.store.view(self.store.get_observation(oid))
            if v.review_status != "confirmed" or v.stale:
                raise ProposalRejected("missing_data", "the observation is no longer confirmed and fresh")
            text = self.brief_text(act.incident_id, oid) or act.parameters["message"]
            inc = self.store.get_incident(act.incident_id)
            alert = self.store.create_alert(Alert(incident_id=act.incident_id, severity="attention", message=text,
                                                  source_observation_ids=[oid], provenance=inc.provenance))
            act.parameters["alert_id"] = alert.id
            act.execution_status = "done"
        act.approval_status = "approved"
        act.parameters["decided_by"] = by
        return self.store.update_action(act, "action_approved")

    def _exists_alert(self, alert_id: str) -> bool:
        try:
            self.store.get_alert(alert_id)
            return True
        except NotFound:
            return False

    # -- observations from the live pipeline ----------------------------------------------------------------
    def evidence_dir(self) -> "Path":
        from pathlib import Path
        base = Path(self.store.path).parent if self.store.path != ":memory:" else REPO_ROOT / "data"
        d = base / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_evidence(self, obs_id: str, box: tuple[float, float, float, float] | None) -> str | None:
        """Save the current drone frame with the box drawn; returns the evidence reference (or None)."""
        import cv2

        vf = self.rt.pipeline.latest_drone
        if vf is None:
            return None
        img = vf.image.copy()
        if box is not None:
            x0, y0, x1, y1 = (int(round(v)) for v in box)
            cv2.rectangle(img, (x0, y0), (x1, y1), (60, 200, 255), 2)
        cv2.imwrite(str(self.evidence_dir() / f"{obs_id}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return f"evidence/{obs_id}.jpg#frame={vf.overlay.frame_id}"

    def observation_from_track(self, incident_id: str, track_id: str, category: str = "person",
                               label: str = "possible person", flagged_by: str = "operator",
                               attributes: dict[str, Any] | None = None) -> "Observation":
        from .models import BoundingBox, Observation, Position, new_id, stale_at

        inc = self.store.get_incident(incident_id)
        p = self.rt.pipeline
        trk = next((t for t in p.tracks_snapshot(self.rt.clock.now_ms()) if t.track_id == track_id), None)
        if trk is None:
            raise ValueError("unknown or expired track")
        prov = PROVENANCE_FROM_WIRE.get(p.counters["drone"].provenance or "", self.wire_provenance())
        box = trk.box_xyxy_px
        pos = None
        unc = None
        method = "none"
        if trk.ground_xy_m is not None and trk.ground_error_m is not None:
            # the incident's ENU frame is the pipeline's calibrated local ground frame (see ARCHITECTURE.md)
            pos = Position(frame=inc.enu_frame, x=float(trk.ground_xy_m[0]), y=float(trk.ground_xy_m[1]))
            unc = float(trk.ground_error_m)
            method = "ground_plane"
        oid = new_id("OBS")
        ev = self.save_evidence(oid, box)
        now = utcnow()
        obs = Observation(
            id=oid, incident_id=incident_id, sensor_source_id="drone", observed_at=now, category=category,  # type: ignore[arg-type]
            label=label, confidence=None,  # the simulated blob detector is not a calibrated detector
            detector=trk.model_id, track_id=track_id,
            bounding_box=BoundingBox(frame="image:drone", x0=box[0], y0=box[1], x1=box[2], y1=box[3]) if box else None,
            world_position=pos, horizontal_uncertainty_m=unc, position_estimation_method=method,  # type: ignore[arg-type]
            evidence_frame_reference=ev, stale_after=stale_at(now, 120.0), provenance=prov,  # type: ignore[arg-type]
            attributes={"flagged_by": flagged_by, "track_state": trk.state, **(attributes or {}),
                        **({"sim_truth": truth} if (truth := self.sim_truth(box)) else {})})
        return self.store.add_observation(obs)

    def store_alert_id(self, pipeline_alert_id: str) -> str:
        """Pipeline alert ids restart every session ("a-1"); the store namespaces them by session."""
        return f"{self.rt.session_id}.{pipeline_alert_id}"

    def acknowledge(self, alert_id: str, by: str = "responder") -> Alert:
        a = self.store.acknowledge_alert(alert_id, by=by)
        prefix = f"{self.rt.session_id}."
        if alert_id.startswith(prefix):
            try:
                self.rt.pipeline.ack_alert(alert_id[len(prefix):])  # the live pipeline alert, same session only
            except Exception:  # noqa: BLE001
                pass
        return a

    def set_responder(self, incident_id: str, pos: dict[str, Any]) -> dict[str, Any]:
        pos = {**pos, "received_at": datetime.now(timezone.utc).isoformat()}
        self.responder[incident_id] = pos
        self.store.append_event(incident_id, "responder_position", pos)
        return pos

    @staticmethod
    def provenance_of(wire: str) -> str:
        return PROVENANCE_FROM_WIRE.get(wire, "simulated")
