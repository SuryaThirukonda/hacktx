"""One incident-scoped clothing mark. Model confidence alone never bypasses current sensor evidence."""
from __future__ import annotations

import math
import re
import uuid
from typing import Any

import cv2

AUTO_THRESHOLD = 0.90
COLORS = "black|white|grey|gray|red|orange|yellow|green|cyan|blue|purple|pink|brown"


def clothing_constraints(description: str) -> dict[str, str]:
    text = description.lower().replace("t-shirt", "shirt").replace("tshirt", "shirt").replace("sweatshirt", "shirt")
    result = {}
    for region, garments in (("torso", "shirt|top|hoodie|jacket|sweater"),
                             ("lower", "pants|trousers|jeans|shorts")):
        match = re.search(rf"\b({COLORS})\s+(?:\w+\s+)?(?:{garments})\b", text)
        if match:
            result[region] = match[1].replace("gray", "grey")
    return result


def supports_automatic_match(description: str) -> bool:
    # Coarse color observations cannot substantiate logos, hats, faces, names, patterns or other extra details.
    words = set(re.findall(r"[a-z]+", description.lower().replace("t-shirt", "shirt").replace("tshirt", "shirt")))
    allowed = set(COLORS.split("|")) | set((
        "hey overwatch please mark find select target track the that this a an one person people guy man woman "
        "boy girl someone wearing wears in with and on his her their is has me can you i would like want to colored coloured "
        "shirt sweatshirt top hoodie jacket sweater pants trousers jeans shorts t"
    ).split())
    return bool(clothing_constraints(description)) and words <= allowed


class PersonMarkService:
    def __init__(self, service):
        self.service = service
        self.rt = service.rt
        self.requests: dict[str, dict[str, Any]] = {}
        self.previews: dict[str, bytes] = {}

    def _fresh(self) -> bool:
        p = self.rt.pipeline
        age = p._age("drone", self.rt.clock.now_ms())
        return age is not None and age <= self.rt.cfg.sources.drone.max_age_ms

    def _current(self, iid: str) -> None:
        inc = self.service.store.get_incident(iid)
        if iid != self.service.current_id or inc.status != "active":
            raise ValueError("marking is available only in the current active incident")

    def request(self, iid: str, description: str) -> dict:
        p = self.rt.pipeline
        with p.lock:
            self._current(iid)
            description = description.strip()
            if not description or len(description) > 500:
                raise ValueError("describe clothing in 1 to 500 characters")
            if not self._fresh():
                raise ValueError("no fresh drone video; restart the stream before requesting a mark")
            if p.crowd:
                raise ValueError("too many people in view to mark a subject")
            if p.autonomy.mode in ("FOLLOW", "HOLD"):
                p.set_autonomy_mode("MANUAL", "new responder mark request")
            if p.locks["subject"].state != "UNLOCKED":
                self.service.follow_release(iid, "replaced by a new responder mark request")
            request_id = uuid.uuid4().hex
            self.previews.clear()  # memory only, one active mark, never retained as a recording
            for t in p.raw_tracks:
                if t.state != "CONFIRMED" or not t.matched_now or t.class_name != "person" or p.last_frame is None:
                    continue
                h, w = p.last_frame.shape[:2]
                x0, y0, x1, y1 = t.measured_box or t.box
                crop = p.last_frame[max(0, int(y0)):min(h, int(y1)), max(0, int(x0)):min(w, int(x1))]
                if crop.size:
                    scale = min(1, 320 / max(crop.shape[:2]))
                    ok, image = cv2.imencode(".jpg", cv2.resize(crop, None, fx=scale, fy=scale))
                    if ok:
                        self.previews[t.track_id] = image.tobytes()
            previous = p.locks["subject"]
            state = {"request_id": request_id, "description": description, "status": "pending",
                     "confidence": None, "engine": None, "track_id": None, "reason": "Matching visible clothing…",
                     "threshold": AUTO_THRESHOLD, "session_id": p.session_id, "created_ms": self.rt.clock.now_ms(),
                     "lock_version": [previous.since_ms, previous.track_id], "auto_approved": False}
            self.requests.clear()
            self.requests[iid] = state
            if not p.describe(description, {"person_mark_request": request_id, "incident_id": iid}):
                state.update(status="no_match", reason="No confirmed people in view, or the decision engine is busy. Try again.")
            self.service.store.append_event(iid, "person_mark_requested", self._public(state))
            return self.view(iid)

    @staticmethod
    def _public(state: dict) -> dict:
        return {k: v for k, v in state.items() if k not in ("lock_version", "session_id")}

    def on_decision(self, job, record) -> None:
        iid = job.context.get("incident_id")
        state = self.requests.get(iid)
        if not state or state["request_id"] != job.context.get("person_mark_request") or state["status"] != "pending":
            return  # newer request or cancellation invalidates every old answer
        p = self.rt.pipeline
        conf = p._conf(record)
        conf = conf if conf is not None and math.isfinite(conf) and 0 <= conf <= 1 else None
        tid = job.context.get("labels", {}).get(record.selected or "")
        state.update(confidence=conf, engine=record.engine, track_id=tid, decision_id=record.decision_id)
        problem = self._candidate_problem(iid, state, check_request_age=True)
        if record.facts.get("_dropped") or record.status != "success":
            state.update(status="error", reason="Decision failed, expired or fell back; no automatic approval.")
        elif not tid:
            state.update(status="no_match", reason="No clear clothing match in view.")
        elif problem:
            state.update(status="review", reason=problem)
        elif record.engine != "openai" or record.mock or not record.acted or conf is None or conf < AUTO_THRESHOLD:
            state.update(status="review", reason="Below 90%, unavailable confidence, or non-OpenAI result: confirmation required.")
        else:
            constraints = clothing_constraints(state["description"])
            candidates = [c for c in job.facts.get("candidates", []) if all(
                c.get(f"{key}_color", "unknown").replace("gray", "grey") == color for key, color in constraints.items())]
            selected = next((c for c in candidates if job.context["labels"].get(c["label"]) == tid), None)
            current = [t for t in p.raw_tracks if t.state == "CONFIRMED" and t.matched_now and t.class_name == "person"
                       and all(p.track_colors.get(t.track_id, ("unknown", "unknown"))[0 if k == "torso" else 1]
                               .replace("gray", "grey") == v for k, v in constraints.items())]
            answer = next((a for a in record.answers if a.type == "choice"), None)
            probs = answer.probabilities if answer else {}
            margin = probs.get(record.selected, 0) - max((v for k, v in probs.items() if k != record.selected), default=0)
            if (not supports_automatic_match(state["description"]) or selected is None or len(candidates) != 1
                    or len(current) != 1 or current[0].track_id != tid):
                state.update(status="review", reason="Clothing is unsupported, mismatched or ambiguous; confirm visually.")
            elif probs and margin < 0.15:
                state.update(status="review", reason="The candidate scores are too close; confirm visually.")
            else:
                self._approve(iid, state, "OpenAI clothing match ≥90%", automatic=True)
        self.service.store.append_event(iid, "person_mark_result", self._public(state))

    def _candidate_problem(self, iid: str, state: dict, check_request_age: bool = False) -> str | None:
        p = self.rt.pipeline
        if iid != self.service.current_id or state["session_id"] != p.session_id or not p.incident.active or p.crowd:
            return "Incident/session changed; request a new mark."
        max_age = self.rt.cfg.decisions.stale_after_s * 1000 if check_request_age else 30000
        if self.rt.clock.now_ms() - state["created_ms"] > max_age:
            return "Match expired; request a fresh match."
        if not self._fresh():
            return "Drone video is stale; request a fresh match."
        lock = p.locks["subject"]
        if state["status"] != "approved" and [lock.since_ms, lock.track_id] != state["lock_version"]:
            return "The operator changed the target while matching; request again."
        info = p._track_info(state.get("track_id"))
        person = any(t.track_id == state.get("track_id") and t.class_name == "person" for t in p.raw_tracks)
        if info is None or info.state != "CONFIRMED" or not info.matched_now or not person:
            return "Proposed person is no longer visible; request a new match."
        return None

    def _approve(self, iid: str, state: dict, by: str, automatic: bool) -> None:
        if self.rt.pipeline.autonomy.mode in ("FOLLOW", "HOLD"):
            self.rt.pipeline.set_autonomy_mode("MANUAL", "new marked target requires explicit follow start")
        self.service.follow_designate(iid, state["track_id"], by)
        state["designation"] = self.service.follow_tracker(iid).s.designation
        state.update(status="approved", auto_approved=automatic, reason="Person marked. Tracking active; aircraft follow requires a separate start.")

    def confirm(self, iid: str, request_id: str) -> dict:
        with self.rt.pipeline.lock:
            self._current(iid)
            state = self.requests.get(iid)
            if not state or state["request_id"] != request_id or state["status"] != "review":
                raise ValueError("no current match awaiting confirmation")
            problem = self._candidate_problem(iid, state)
            if problem:
                raise ValueError(problem)
            self._approve(iid, state, "responder confirmed clothing match", automatic=False)
            self.service.store.append_event(iid, "person_mark_confirmed", self._public(state))
            return self.view(iid)

    def release(self, iid: str) -> dict:
        with self.rt.pipeline.lock:
            self._current(iid)
            state = self.requests.pop(iid, None)
            if state and state["status"] == "approved" and self.service.follow_tracker(iid).s.designation == state.get("designation"):
                self.service.follow_release(iid, "responder released mark")
            self.previews.clear()
            self.service.store.append_event(iid, "person_mark_released", {})
            return self.view(iid)

    def view(self, iid: str) -> dict:
        with self.rt.pipeline.lock:
            p = self.rt.pipeline
            state = self.requests.get(iid)
            result = self._public(state) if state else {"status": "idle", "threshold": AUTO_THRESHOLD}
            if state and state["status"] == "pending" and self.rt.clock.now_ms() - state["created_ms"] > 10000:
                state.update(status="error", reason="Matching timed out; try again.")
                result = self._public(state)
            follow = self.service.follow_tracker(iid).view(self.rt.clock.now_ms())
            marked = state and state["status"] == "approved"
            if marked:
                self.service.follow_update(iid)
                follow = self.service.follow_tracker(iid).view(self.rt.clock.now_ms())
                if iid != self.service.current_id or state["session_id"] != p.session_id or p.locks["subject"].state == "UNLOCKED":
                    result.update(status="released", reason="Target released or session restarted.")
                elif state.get("designation") != self.service.follow_tracker(iid).s.designation:
                    result.update(status="superseded", reason="Target binding changed; verify it on the dashboard.")
            auto = p.autonomy.state().model_dump()
            ready = bool(marked and result["status"] == "approved" and follow["status"] == "tracking"
                         and self._fresh() and p._autonomy_inputs(self.rt.clock.now_ms()).subject_verified and p.autonomy.commands_enabled
                         and p.autonomy.mode != "ESTOP" and not p._real_flight_problem(self.rt.clock.now_ms()))
            blocker = None
            bridge = p.autonomy.bridge
            if ready and not bridge.connected(self.rt.clock.now_ms()):
                blocker = "Aircraft bridge link is disconnected."
            elif ready and self.rt.mode == "LIVE" and (p._age("telemetry", self.rt.clock.now_ms()) is None
                    or p._age("telemetry", self.rt.clock.now_ms()) > self.rt.cfg.sources.telemetry.max_age_ms):
                blocker = "Aircraft telemetry is stale or missing."
            elif ready and not p.autonomy.target_is_sim and p.cfg.autonomy.motion_profile == "full" \
                    and p._autonomy_inputs(self.rt.clock.now_ms()).subject_xy is None:
                blocker = "Full-motion follow needs a calibrated ground position for the marked person."
            if not ready:
                blocker = p._real_flight_problem(self.rt.clock.now_ms())
                if not blocker:
                    if getattr(bridge, "control_inhibited", False):
                        blocker = "Pilot control is latched. Neutralize the RC and re-arm on the website."
                    elif not p.autonomy.commands_enabled:
                        blocker = "Use the updated control bridge and DJI simulator, or explicitly enable real flight."
                    else:
                        blocker = "Select a current verified mark with fresh video before starting movement."
            ready = ready and blocker is None
            return {**result, "follow": follow, "autonomy": auto, "can_start_follow": ready,
                    "movement_blocker": blocker,
                    "preview_available": bool(state and state.get("track_id") in self.previews)}

    def start_follow(self, iid: str, request_id: str) -> dict:
        with self.rt.pipeline.lock:
            self._current(iid)
            state = self.view(iid)
            if state.get("request_id") != request_id or not state["can_start_follow"]:
                raise ValueError("follow needs the current verified mark, fresh sensors and an enabled, locally re-armed bridge")
            self.rt.pipeline.set_autonomy_mode("FOLLOW", "responder started marked-person follow")
            return self.view(iid)
