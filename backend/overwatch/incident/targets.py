"""Incident-owned coordination of the existing locks, object memory and SAR mission.

This component never arms aircraft, starts follow, or sends bridge commands.
"""
from __future__ import annotations

import math
from dataclasses import replace

from ..tracking.memory import SubjectMemory
from ..vision.quality import measurement_problems


class TargetCoordinator:
    def __init__(self, service):
        self.service = service
        self.rt = service.rt
        self.subject: SubjectMemory | None = None
        self._last_event = None
        self._last_saved_ms = -1e12
        self._rebind_track = None
        self._rebind_frames: set[int] = set()
        from ..missions.sar import SARMission
        self.sar = SARMission(service)

    def _archive(self, reason: str) -> None:
        if self.subject and self.subject.view:
            old = self.subject.snapshot()
            old.update(state="LOST", measured_position=None, current_track_id=None,
                       position_kind="last_seen", archived_reason=reason)
            self.service.store.save_target(old["incident_id"], old, "target_archived")
        self.subject = None
        self._last_event = None
        self._rebind_track = None
        self._rebind_frames.clear()

    def update(self) -> None:
        p = self.rt.pipeline
        with p.lock:
            inc = self.service.current()
            if (inc is None or inc.status != "active" or not p.incident.active
                    or p.incident.incident_id != inc.id):
                self._archive("incident closed or replaced")
                return
            if self.subject and (self.subject.incident_id != inc.id or self.subject.session_id != p.session_id):
                self._archive("incident/session changed")
            self.sar.refresh_memory(inc)
            self.service.person_mark.refresh_continuous(inc.id)
            lock = p.locks["subject"]
            if not lock.active:
                self._archive("designation released")
                return
            if self.subject and self.subject.designation_ms != lock.since_ms:
                self._archive("new designation")
            if self.subject is None:
                obj = p.memory.object_for_track(lock.track_id) if lock.track_id else None
                oid = obj.oid if obj and obj.link != "possible" else None
                self.subject = SubjectMemory(inc.id, p.session_id, inc.enu_frame, lock, oid)
                self._last_saved_ms = -1e12
            raw = next((t for t in p.raw_tracks if t.track_id == lock.track_id), None)
            quality = measurement_problems(raw, p.raw_tracks, p.last_frame_size,
                                           self.rt.cfg.perception.tracker.high_thresh, p.last_detections)
            age = p._age("drone", self.rt.clock.now_ms())
            capture = p.counters["drone"].last_capture_ms
            capture_age = self.rt.clock.now_ms() - capture if capture is not None else float("inf")
            fresh = (age is not None and age <= self.rt.cfg.sources.drone.max_age_ms
                     and -100 <= capture_age <= self.rt.cfg.sources.drone.max_age_ms)
            info = p._track_info(lock.track_id) if lock.track_id else None
            position_filter = p.world_kf.get(lock.track_id)
            if info and (not p.calib.valid or position_filter is None or capture is None
                         or abs(position_filter.last_update_ms - capture) > 1):
                info = replace(info, world_xy=None, world_err=None)
            self._simulation_reacquire(lock, info, fresh, quality)
            view = self.subject.update(lock, info, self.rt.clock.now_ms(), p.last_frame_id,
                                       p.counters["drone"].last_capture_ms, fresh, quality,
                                       p.track_hist.get(lock.track_id))
            view["source_provenance"] = p.counters["drone"].provenance
            self.subject.view["source_provenance"] = view["source_provenance"]
            key = (view["state"], view["last_track_id"], view["binding_verified"], tuple(view["quality_reasons"]))
            changed = key != self._last_event
            now = self.rt.clock.now_ms()
            if changed or now - self._last_saved_ms >= 1000:
                self.service.store.save_target(inc.id, view, "target_state" if changed else "target_snapshot")
                self._last_saved_ms, self._last_event = now, key

    def _simulation_reacquire(self, lock, info, fresh: bool, quality: tuple[str, ...]) -> None:
        """Allow only repeated, unique, measured continuity in synthetic simulation.

        Ambiguous alternatives or missing geometry stay unverified. Physical locks and
        flight_confirmed_track_id are never modified by this automatic path.
        """
        p = self.rt.pipeline
        if (self.rt.mode != "SIMULATED" or self.rt.sim is None or not p.autonomy.target_is_sim
                or p.counters["drone"].provenance != "SYNTHETIC" or lock.verified):
            return
        if self._rebind_track != lock.track_id:
            self._rebind_track = lock.track_id
            self._rebind_frames.clear()
        if (not fresh or quality or info is None or info.world_xy is None or lock.kf is None
                or info.track_id in getattr(lock, "rejected", set())):
            self._rebind_frames.clear()
            return
        if (not self.subject or self.subject.last_seen_ms is None
                or self.rt.clock.now_ms() - self.subject.last_seen_ms > 10000
                or lock.crossing(self.rt.clock.now_ms()) and lock.motion_consistent(info) is not True):
            self._rebind_frames.clear()
            return
        candidates = []
        pred = lock.kf.position
        for raw in p.raw_tracks:
            if (raw.class_name != "person" or raw.track_id == p.locks["responder"].track_id
                    or raw.track_id in getattr(lock, "rejected", set())):
                continue
            other = p._track_info(raw.track_id)
            if other is None or not other.matched_now or other.state != "CONFIRMED" or other.world_xy is None:
                continue
            if (math.dist(other.world_xy, pred) <= lock.cfg.search_radius_m
                    and lock.gallery_similarity(other.hist) >= lock.cfg.rebind_similarity
                    and lock.motion_consistent(other) is not False):
                candidates.append(other.track_id)
        if candidates != [lock.track_id] or lock.gallery_similarity(info.hist) < lock.cfg.rebind_similarity + .10:
            self._rebind_frames.clear()
            return
        self._rebind_frames.add(p.last_frame_id)
        if len(self._rebind_frames) >= 3:
            p.pending_lock_events.extend(lock.confirm_candidate(info, self.rt.clock.now_ms(), "simulation continuity rules"))
            self.service.store.append_event(self.service.current_id, "target_continuity_accepted",
                                            {"logical_subject_id": self.subject.logical_id,
                                             "track_id": lock.track_id, "human_confirmed": False,
                                             "basis": "unique position, appearance, motion and three measured frames"})
            self._rebind_frames.clear()

    def view(self, incident_id: str) -> dict:
        p = self.rt.pipeline
        active = (self.subject.snapshot() if self.subject and self.subject.incident_id == incident_id
                  and self.subject.session_id == p.session_id and incident_id == self.service.current_id
                  and p.incident.active else None)
        request = self.service.person_mark.requests.get(incident_id)
        search = self.service.person_mark._public(request) if request else None
        if search and request.get("session_id") != p.session_id:
            search = {**search, "status": "released", "reason": "session changed"}
        state = active["state"] if active else ("CANDIDATE" if search and search.get("candidates") else "SEARCHING")
        return {"incident_id": incident_id, "session_id": p.session_id, "state": state, "subject": active,
                "search": search, "history": self.service.store.list_targets(incident_id)}
