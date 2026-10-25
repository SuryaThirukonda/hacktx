"""Interpretable SAR candidates from measured people + explicitly simulated map context.

No actor roles/distress labels are read. Detector evidence, environmental context,
triage score, human review and optional evaluation truth remain separate records.
"""
from __future__ import annotations

import math

from ..decisions.tasks import rules_d7
from ..incident.briefing import responder_targets
from ..incident.models import utcnow
from ..tracking.memory import SubjectMemory, logical_object_id
from ..vision.quality import measurement_problems
from .sar_guidance import in_grid
from .crew_routing import assessed_route


def triage_score(context: dict, stationary_s: float, cfg) -> dict:
    reasons = []
    if context.get("flood_depth_m", 0) >= cfg.flood_depth_m:
        reasons.append({"code": "flood_exposure", "text": "Measured location overlaps simulated flood water",
                        "weight": cfg.flood_weight})
    if context.get("fire_keep_out", False):
        reasons.append({"code": "fire_proximity", "text": "Measured location is in a simulated fire keep-out area",
                        "weight": cfg.fire_weight})
    if stationary_s >= cfg.stationary_s:
        reasons.append({"code": "stationary", "text": "Position has remained approximately stationary",
                        "weight": cfg.stationary_weight})
    value = sum(r["weight"] for r in reasons)
    return {"value": value, "threshold": cfg.threshold, "flag": value >= cfg.threshold,
            "reasons": reasons, "kind": "deterministic assistance triage, not a medical/intent classifier"}


class SARMission:
    def __init__(self, service):
        self.service, self.rt = service, service.rt
        self.frames: dict[str, set[int]] = {}
        self.last_vote: dict[str, float] = {}
        self.stationary: dict[str, tuple[float, tuple[float, float]]] = {}
        self._scope = None
        self._guidance_cache = {}
        self.memories: dict[str, SubjectMemory] = {}
        self._memory_events = {}
        self._memory_saved_ms = {}

    def _models(self):
        sim = self.rt.sim
        layers = sim.scenario.hazards if sim is not None else None
        return getattr(layers, "flood", None), getattr(layers, "fire", None)

    def run(self, inc) -> None:
        p, cfg = self.rt.pipeline, self.rt.cfg.sar
        if (not cfg.enabled or self.rt.mode != "SIMULATED" or self.rt.sim is None
                or (inc.mission_mode != "search" and not p.rescue.active()) or inc.provenance != "simulated"):
            return
        scope = (inc.id, p.session_id)
        if scope != self._scope:
            for memory in self.memories.values():
                old = memory.snapshot()
                if old:
                    old.update(state="LOST", measured_position=None, current_track_id=None,
                               position_kind="last_seen", archived_reason="incident/session changed")
                    self.service.store.save_target(old["incident_id"], old, "target_archived")
            self.frames.clear()
            self.last_vote.clear()
            self.stationary.clear()
            self._guidance_cache.clear()
            self.memories.clear()
            self._memory_events.clear()
            self._memory_saved_ms.clear()
            self._scope = scope
        flood, fire = self._models()
        if flood is None and fire is None and not p.rescue.active():
            return
        # Explicitly simulated responder GPS service, separate from victim perception.
        previous = self.service.responder.get(inc.id)
        actor = self.rt.sim.actor("responder")
        if actor is not None and (previous is None or previous.get("source") == "simulated"):
            self.service.set_responder(inc.id, {"source": "simulated", "accuracy_m": 0.5,
                "enu": {"frame": inc.enu_frame, "x": float(actor.pos[0]), "y": float(actor.pos[1]), "z": 0},
                "heading_deg": float(actor.heading) % 360, "heading_accuracy_deg": 5.0})
        now = self.rt.clock.now_ms()
        age = p._age("drone", now)
        capture = p.counters["drone"].last_capture_ms
        fresh = (age is not None and age <= self.rt.cfg.sources.drone.max_age_ms
                 and capture is not None and -100 <= now - capture <= self.rt.cfg.sources.drone.max_age_ms)
        self._sync_memory(inc, fresh)
        if (age is None or age > self.rt.cfg.sources.drone.max_age_ms
                or capture is None or not -100 <= now - capture <= self.rt.cfg.sources.drone.max_age_ms
                or p.counters["drone"].provenance != "SYNTHETIC" or not p.calib.valid):
            self.frames.clear()
            return
        seen = {v.observation.attributes["sar"]["logical_subject_id"]
                for v in self.service.store.list_observations(inc.id) if v.observation.attributes.get("sar")}
        with p.lock:
            for raw in p.raw_tracks:
                if (raw.class_name != "person" or raw.track_id == p.locks["responder"].track_id
                        or raw.state != "CONFIRMED" or not raw.matched_now):
                    continue
                problems = measurement_problems(raw, p.raw_tracks, p.last_frame_size,
                                                self.rt.cfg.perception.tracker.high_thresh, p.last_detections)
                info = p._track_info(raw.track_id)
                kf = p.world_kf.get(raw.track_id)
                if (problems or info is None or info.world_xy is None or info.world_err is None
                        or kf is None or abs(kf.last_update_ms - capture) > 1):
                    continue
                obj = p.memory.object_for_track(raw.track_id)
                # Ambiguous object reattachment is not a new victim or a confirmed continuation.
                if obj is not None and obj.link == "possible":
                    continue
                key = logical_object_id(inc.id, p.session_id, obj.oid if obj else raw.track_id)
                if key in seen:
                    beacon = p.rescue.beacon if p.rescue.active() else None
                    if beacon and beacon["track_id"] == raw.track_id:
                        existing = next((v.observation for v in self.service.store.list_observations(inc.id)
                                         if v.observation.attributes.get("sar", {}).get("logical_subject_id") == key), None)
                        if existing:
                            beacon.update(observation_id=existing.id, logical_subject_id=key)
                    continue
                xy = tuple(info.world_xy)
                t0, origin = self.stationary.setdefault(key, (now, xy))
                if math.dist(origin, xy) > 1.0:
                    self.stationary[key] = (now, xy)
                    t0 = now
                context = {"source": "simulated environmental map", "position_source": "measured calibrated detection",
                           "sample_ts_ms": now, "flood_depth_m": None, "fire_keep_out": False}
                if flood and in_grid(flood.grid, *xy):
                    context["flood_depth_m"] = round(flood.depth(*xy, self.rt.sim.t_s), 3)
                if fire and in_grid(fire.grid, *xy):
                    r, c = fire.grid.cell(*xy)
                    context["fire_keep_out"] = bool(fire.arrival()[r, c] <= self.rt.sim.t_s + 120)
                score = triage_score({k: v for k, v in context.items() if v is not None}, (now - t0) / 1000, cfg)
                beacon = p.rescue.beacon if p.rescue.active() else None
                if beacon and beacon["track_id"] == raw.track_id:
                    score = {"value": max(3., score["value"]), "flag": True,
                             "reasons": score["reasons"] + [{"code": "reported_person", "weight": 3.,
                             "text": "Observed clothing matches the simulated missing-person request"}]}
                if not score["flag"]:
                    self.frames.pop(key, None)
                    continue
                votes = self.frames.setdefault(key, set())
                if now - self.last_vote.get(key, now) > 2000:
                    votes.clear()
                votes.add(p.last_frame_id)
                self.last_vote[key] = now
                if len(votes) < cfg.min_frames:
                    continue
                obs = self.service.observation_from_track(
                    inc.id, raw.track_id, "person", "possible assistance candidate", "sar_rules",
                    {"logical_subject_id": key, "sar": {"logical_subject_id": key,
                     "detected_observation": {"track_id": raw.track_id, "frame_id": p.last_frame_id,
                                               "capture_ts_ms": p.counters["drone"].last_capture_ms},
                     "environment": context, "score": score, "human_review_required": True}})
                self.service.store.append_event(inc.id, "sar_candidate", {"observation_id": obs.id,
                                                 "logical_subject_id": key, "score": score, "environment": context})
                if beacon and beacon["track_id"] == raw.track_id:
                    beacon.update(observation_id=obs.id, logical_subject_id=key)
                seen.add(key)
                self.memories[key] = SubjectMemory(inc.id, p.session_id, inc.enu_frame, object_id=obj.oid if obj else raw.track_id)
            self._sync_memory(inc, fresh)

    def _sync_memory(self, inc, fresh):
        """Persist observed and predicted SAR subjects through the shared SubjectMemory interface."""
        p = self.rt.pipeline
        with p.lock:
            for key, memory in self.memories.items():
                selected = self.service.targets.subject
                if selected and selected.logical_id == key:
                    continue  # selected-subject coordinator is the sole writer for this same logical subject
                obj = next((o for o in p.memory.objects.values()
                            if logical_object_id(inc.id, p.session_id, o.oid) == key), None)
                tid = obj.track_id if obj else None
                raw = next((t for t in p.raw_tracks if t.track_id == tid), None)
                quality = measurement_problems(raw, p.raw_tracks, p.last_frame_size,
                                               self.rt.cfg.perception.tracker.high_thresh, p.last_detections)
                info = p._track_info(tid) if tid else None
                kf = p.world_kf.get(tid)
                capture = p.counters["drone"].last_capture_ms
                if kf is None or capture is None or abs(kf.last_update_ms - capture) > 1 or not p.calib.valid:
                    info = None
                view = memory.update_object(obj, info, self.rt.cfg.locks, self.rt.clock.now_ms(), p.last_frame_id,
                                            capture, fresh, quality, p.track_hist.get(tid))
                previous = self._memory_events.get(key)
                marker = (view["state"], view["last_track_id"], view["last_verified_ts_ms"])
                transition = previous is None or previous[:2] != marker[:2]
                if previous != marker and (transition or self.rt.clock.now_ms() - self._memory_saved_ms.get(key, -1e12) >= 1000):
                    view["source_provenance"] = p.counters["drone"].provenance
                    self.service.store.save_target(inc.id, view, "target_state" if not previous or previous[0] != marker[0] else "target_snapshot")
                    self._memory_events[key] = marker
                    self._memory_saved_ms[key] = self.rt.clock.now_ms()

    def refresh_memory(self, inc):
        """Advance existing SAR read models at sensor/publication cadence, independently of persistence."""
        p = self.rt.pipeline
        if (self.rt.mode != "SIMULATED" or self._scope != (inc.id, p.session_id)
                or inc.mission_mode != "search" or not self.memories):
            return
        now = self.rt.clock.now_ms()
        age, capture = p._age("drone", now), p.counters["drone"].last_capture_ms
        fresh = (age is not None and age <= self.rt.cfg.sources.drone.max_age_ms and capture is not None
                 and -100 <= now-capture <= self.rt.cfg.sources.drone.max_age_ms
                 and p.counters["drone"].provenance == "SYNTHETIC")
        self._sync_memory(inc, fresh)

    def current_memory(self, key, incident_id):
        """Current read model for live guidance; persisted snapshots remain the historical fallback."""
        p = self.rt.pipeline
        selected = self.service.targets.subject
        if (selected and selected.logical_id == key and selected.incident_id == incident_id
                and selected.session_id == p.session_id):
            return selected.snapshot()
        if self._scope == (incident_id, p.session_id) and key in self.memories:
            return self.memories[key].snapshot()
        return next((m for m in self.service.store.list_targets(incident_id) if m["logical_subject_id"] == key), None)

    def guidance(self, obs) -> dict:
        inc = self.service.store.get_incident(obs.incident_id)
        p = self.rt.pipeline
        out = {"mode": "simulated_navigation", "frame": inc.enu_frame, "position_kind": "last_observed",
               "route": {"status": "unavailable", "points": []}, "responder_position": None,
               "target_position": obs.world_position.model_dump() if obs.world_position else None,
               "callout": "Simulation: location is unverified. Refer to the observation/video; no precise route."}
        v = self.service.store.view(obs)
        if v.review_status != "confirmed":
            return {**out, "callout": ("Assistance candidate rejected; no rescue dispatch."
                                       if v.review_status == "rejected" else
                                       "Possible assistance candidate; awaiting human confirmation.")}
        if (self.rt.mode != "SIMULATED" or self.rt.sim is None or self.service.current_id != inc.id
                or obs.attributes.get("session_id") != p.session_id or inc.provenance != "simulated"):
            return {**out, "callout": "Historical or non-simulated observation; no current simulated guidance."}
        responder = self.service.responder.get(inc.id)
        brief = responder_targets(self.service.store, inc.id, responder)
        target = next((t for t in brief["targets"] if t["observation_id"] == obs.id), None)
        if not target or not target.get("grounded") or not responder or responder.get("source") != "simulated":
            return out
        enu = responder.get("enu")
        if enu is None or enu.get("frame") != inc.enu_frame:
            return out
        key = obs.attributes["sar"]["logical_subject_id"]
        memory = self.current_memory(key, inc.id)
        current_position = memory.get("measured_position") if memory else None
        age = p._age("drone", self.rt.clock.now_ms())
        if memory and (age is None or age > self.rt.cfg.sources.drone.max_age_ms
                       or memory["last_verified_ts_ms"] is None
                       or self.rt.clock.now_ms() - memory["last_verified_ts_ms"] > self.rt.cfg.sources.drone.max_age_ms):
            return {**out, "target_memory": memory, "callout": "Simulation: subject measurement is stale. "
                    "Last-seen location only; hold and review before approaching."}
        if memory and (memory["state"] != "VISIBLE" or current_position is None
                       or current_position.get("frame") != inc.enu_frame):
            return {**out, "target_memory": memory, "callout": "Simulation: confirmed subject is not currently measured. "
                    "Last-seen/predicted location only; hold and review before approaching."}
        if memory and current_position:
            from ..incident.briefing import cardinal, clock_of
            dx, dy = current_position["x"] - enu["x"], current_position["y"] - enu["y"]
            north = inc.coordinate_reference.north_offset_deg if inc.coordinate_reference else 0
            bearing = (math.degrees(math.atan2(dx, dy)) + north) % 360
            target.update(range_m=round(math.hypot(dx, dy), 1), bearing_deg=round(bearing), cardinal=cardinal(bearing),
                          uncertainty_m=round(math.hypot(memory["uncertainty_m"], responder["accuracy_m"]), 1),
                          last_observed_s=round(max(0, self.rt.clock.now_ms()-memory["last_verified_ts_ms"])/1000))
            if brief["responder"]["heading_trusted"]:
                target["clock"] = clock_of(bearing - responder["heading_deg"])
        start = (enu["x"], enu["y"])
        end = (current_position["x"], current_position["y"]) if current_position else (obs.world_position.x, obs.world_position.y)
        flood, fire = self._models()
        # A fresh route is computed at most once per simulated second and responder position.
        cache_key = (obs.id, int(self.rt.sim.t_s), tuple(start), end)
        route = self._guidance_cache.get(cache_key)
        if route is None:
            route = assessed_route(start, end, flood=flood, fire=fire, world=self.rt.sim.world, t_s=self.rt.sim.t_s,
                                 max_depth_m=self.rt.cfg.sar.route_max_depth_m,
                                 approach_radius_m=self.rt.cfg.sar.approach_radius_m,
                                 fire_margin_s=getattr(self.rt.sim.scenario.hazards, "margin", 120.),
                                 walk_mps=(self.rt.sim.scenario.rescue.get("crew_speed_mps", 3)
                                           if self.rt.sim.scenario.rescue else None))
            self._guidance_cache = {k: v for k, v in self._guidance_cache.items()
                                    if k[1] >= int(self.rt.sim.t_s) - 1}
            self._guidance_cache[cache_key] = route
        rescue = p.rescue
        if rescue.active() and rescue.beacon and rescue.beacon.get("observation_id") == obs.id:
            route = rescue.assess(start, self.rt.clock.now_ms())
        instruction = {"access_route": "Follow the marked access route assessed against the simulated hazard map.",
                       "approach_only": "Follow the marked route only to the approach point; do not enter the hazard.",
                       "blocked": "No assessed access route. Hold for specialist rescue/access review.",
                       "unavailable": "No assessed route; refer to the map and request access review."}[route["status"]]
        return {**out, "route": route, "responder_position": enu,
                "target_position": current_position or obs.world_position.model_dump(),
                "position_kind": "measured" if current_position else "last_observed", "target_memory": memory,
                "distance_m": target["range_m"], "bearing_deg": target["bearing_deg"],
                "clock": target.get("clock"), "uncertainty_m": target["uncertainty_m"],
                "callout": (f"Simulation: assistance location confirmed. Approximately {target['range_m']:.0f} metres "
                            f"{target['cardinal']}, plus or minus {target['uncertainty_m']:.0f}. {instruction} "
                            f"Location last observed {target['last_observed_s']} seconds ago.")}

    def view(self, incident_id: str) -> dict:
        views = [v for v in self.service.store.list_observations(incident_id) if v.observation.attributes.get("sar")]
        alerts = self.service.store.list_alerts(incident_id)
        inc = self.service.store.get_incident(incident_id)
        responder = self.service.responder.get(incident_id, {}).get("enu")
        leads = []
        if responder and responder.get("frame") == inc.enu_frame:
            for v in views:
                pos = v.observation.world_position
                if pos is None or pos.frame != inc.enu_frame:
                    continue
                leads.append({"id": v.observation.id, "age_s": max(0, (utcnow() - v.observation.observed_at).total_seconds()),
                              "distance_m": math.hypot(pos.x - responder["x"], pos.y - responder["y"]),
                              "uncertainty_m": v.observation.horizontal_uncertainty_m, "reviewed": v.review_status != "pending"})
        first = rules_d7({"leads": leads})["first_lead"]
        items = []
        for v in views:
            obs = v.observation
            alert = next((a for a in reversed(alerts) if obs.id in a.source_observation_ids), None)
            items.append({"observation": obs.model_dump(mode="json"), "review_status": v.review_status,
                          "target_memory": self.current_memory(obs.attributes["sar"]["logical_subject_id"], incident_id),
                          "guidance": self.guidance(obs), "dispatch": alert.model_dump(mode="json") if alert else None})
        return {"incident_id": incident_id, "mode": "simulated_navigation", "first_review": first,
                "items": sorted(items, key=lambda x: x["observation"]["id"] != first)}
