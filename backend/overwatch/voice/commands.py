"""Operator commands (spoken or typed) to mission primitives, never to motion.

1. The transcript arrives with provenance ``manual``.
2. Intent: decision task D13_intent, a choice over a closed verb set (FOLLOW, SEARCH, GOTO, ORBIT, LOOK, HOLD, RTL,
   FIND, UNCLEAR) with three predicates. The keyword grammar is the deterministic baseline and the default engine;
   below confidence 0.7, or on UNCLEAR, the system asks a clarifying question and does nothing. HOLD and RTL are
   immediate and bypass the model entirely.
3. Grounding (``voice/grounding.py``): places and people resolve against named world entities; two plausible ones
   are always a question, never a guess.
4. A mission primitive with validated parameters, shaped like a MissionAction (proposed by a human, an approval
   status, safety constraints): FOLLOW(track), SEARCH(sector | polygon), GOTO(landmark | point), FIND(descriptor),
   HOLD, RTL. Targets pass the safety supervisor's viewpoint check; every command the aircraft gets still goes
   through the supervisor. FOLLOW needs a person's approval always (it is an identity choice); SEARCH and GOTO need
   it for a real aircraft (and in the simulator when asked); FIND only sets who to look for (evidence on the pursuit
   belief), so it needs none.
5. Execution maps onto what exists: FOLLOW locks the track and enters FOLLOW (TRACKING), SEARCH flies a sweep of the
   sector, GOTO flies to the place and looks at it, FIND sets the "look for" description, HOLD and RTL are modes.

Every step is an incident event: the transcript, the parsed intent and its confidence, the primitive, the approval
and the result. ORBIT and LOOK are recognised but not built in this version: the reply says so.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from ..decisions.tasks import INTENT_GATE, d13_intent, intent_grammar
from ..ids import IdGen
from ..vision.appearance import parse_description
from .grounding import Entity, Grounding, pick_option

if TYPE_CHECKING:
    from ..pipeline import Pipeline

IMPLEMENTED = ("FOLLOW", "SEARCH", "GOTO", "FIND", "HOLD", "RTL")
APPROVAL = {"FOLLOW": "always", "SEARCH": "live", "GOTO": "live", "FIND": "never", "HOLD": "never", "RTL": "never"}
CONSTRAINTS = {
    "FOLLOW": ["a person confirms who is followed", "safety supervisor clamps every command"],
    "SEARCH": ["the sweep stays inside the geofence", "safety supervisor clamps every command"],
    "GOTO": ["the target is inside the geofence", "safety supervisor clamps every command"],
    "FIND": ["sets who to look for only: clothing words, never a face", "no flight"],
    "HOLD": ["immediate: no model is asked"],
    "RTL": ["immediate: no model is asked"],
}
SEARCH_BOX_M = 45.0  # SEARCH around a place that is not a sector: a square this far either side


class VoiceCommands:
    def __init__(self, pipeline: "Pipeline") -> None:
        self.p = pipeline
        self.entities_provider: Any = None  # callable() -> list[Entity] (the runtime attaches the world's)
        self.require_approval_sim = False  # ask for approval of SEARCH and GOTO in the simulator too (demo toggle)
        self.reset()

    def reset(self) -> None:
        self.ids = IdGen("vc")
        self.cmds: deque[dict[str, Any]] = deque(maxlen=40)
        self.question: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None

    # -- helpers ------------------------------------------------------------------------------------------------
    def _now(self) -> float:
        return self.p.clock.now_ms()

    def _emit(self, kind: str, **payload: Any) -> None:
        self.p.outbox.append({"type": "voice", "kind": kind, "ts_ms": self._now(), "provenance": "manual", **payload})

    def _cmd(self, cid: str) -> dict[str, Any] | None:
        return next((c for c in reversed(self.cmds) if c["id"] == cid), None)

    def _reply(self, cmd: dict[str, Any], status: str, text: str) -> dict[str, Any]:
        cmd["status"], cmd["reply"] = status, text
        self._emit("voice_reply", id=cmd["id"], status=status, reply=text)
        return cmd

    def _ask(self, cmd: dict[str, Any], question: str, options: list[Entity] | None = None, intent: str | None = None
             ) -> dict[str, Any]:
        self.question = {"cmd": cmd["id"], "question": question, "intent": intent or cmd.get("intent"),
                         "options": [o.view() | {"_entity": o} for o in (options or [])], "ts_ms": self._now()}
        cmd["question"] = question
        cmd["options"] = [o.view() for o in (options or [])]
        self._emit("voice_question", id=cmd["id"], question=question, options=cmd["options"])
        return self._reply(cmd, "asking", question)

    def _grounding(self) -> Grounding:
        p = self.p
        d = p.drone_state(self._now())
        drone_xy = tuple(d.position_xy_m) if d and d.position_xy_m else None
        fp = d.footprint_xy_m if (d is not None and p.calib.valid) else None

        def in_view(xy: tuple[float, float]) -> bool:
            from ..autonomy.safety import inside
            return bool(fp) and inside(fp, xy[0], xy[1])

        ents = list(self.entities_provider() if self.entities_provider else [])
        return Grounding(ents, drone_xy, in_view if fp else None)

    def _people(self) -> list[dict[str, Any]]:
        p = self.p
        resp = p.locks["responder"].track_id
        out = []
        for t in p.raw_tracks:
            if t.class_name != "person" or t.state != "CONFIRMED" or not t.matched_now or t.track_id == resp:
                continue
            kf = p.world_kf.get(t.track_id)
            from ..tracking.lock import short
            out.append({"track_id": t.track_id, "label": short(t.track_id), "hist": p.track_hist.get(t.track_id),
                        "colors": p.track_colors.get(t.track_id), "xy": tuple(kf.position) if kf else None})
        return out

    # -- 1. a transcript arrives ----------------------------------------------------------------------------------
    def handle(self, text: str, source: str = "dashboard", by: str = "operator") -> dict[str, Any]:
        with self.p.lock:
            t = " ".join((text or "").split())
            cmd: dict[str, Any] = {"id": self.ids(), "transcript": t, "source": source, "by": by, "ts_ms": self._now(),
                                   "provenance": "manual", "status": "received"}
            self.cmds.append(cmd)
            self._emit("voice_transcript", id=cmd["id"], transcript=t, source=source, by=by)
            if not t:
                return self._reply(cmd, "ignored", "Nothing heard.")
            g = intent_grammar(t)
            cmd["grammar"] = g
            # an answer to the question just asked ("the first one", "the one on Oak Street")
            if self.question is not None and (g["confidence"] < INTENT_GATE or g["intent"] == self.question["intent"]):
                opt = pick_option(t, self.question["options"])
                if opt is not None:
                    q = self.question
                    self.question = None
                    orig = self._cmd(q["cmd"]) or cmd
                    cmd.update(intent=q["intent"], confidence=1.0, engine="answer to a question", answers=q["cmd"])
                    self._emit("voice_intent", id=cmd["id"], intent=q["intent"], confidence=1.0, engine=cmd["engine"],
                               answers=q["cmd"])
                    return self._propose_for(cmd, q["intent"], opt["_entity"], orig.get("transcript", t))
            # HOLD and RTL are immediate: no model is asked
            if g["intent"] in ("HOLD", "RTL") and g["confidence"] >= INTENT_GATE:
                self.question = None
                cmd.update(intent=g["intent"], confidence=g["confidence"], engine="grammar (immediate, no model)",
                           predicates={k: g[k] for k in ("has_location_reference", "has_person_description", "is_cancel")})
                self._emit("voice_intent", id=cmd["id"], intent=g["intent"], confidence=g["confidence"],
                           engine=cmd["engine"], predicates=cmd["predicates"])
                return self._propose(cmd, g["intent"], {})
            job = d13_intent({"transcript": t, "mission_profile": self.p.profile,
                              "autonomy_mode": self.p.autonomy.mode})
            job.context = {"voice_id": cmd["id"]}
            if self.p.cfg.decisions.voice_engine == "rules":
                job.context["engine"] = "rules"
            cmd["status"] = "parsing"
            if self.p.decisions is None or not self.p.decisions.submit(job):
                self.on_intent(job, None)  # no decision service (or the same words just now): the grammar answers
            return cmd

    # -- 2. the intent is back -------------------------------------------------------------------------------------
    def on_intent(self, job: Any, rec: Any) -> None:
        with self.p.lock:
            cmd = self._cmd(job.context.get("voice_id", ""))
            if cmd is None or cmd.get("status") not in ("parsing", "received"):
                return
            g = cmd.get("grammar") or intent_grammar(cmd["transcript"])
            if rec is None:
                intent, conf, engine, did = g["intent"], g["confidence"], "grammar", None
                preds = {k: g[k] for k in ("has_location_reference", "has_person_description", "is_cancel")}
            else:
                engine, did = rec.engine, rec.decision_id
                ans = next((a for a in rec.answers if a.name == "intent"), None)
                if rec.engine == "rules":  # the keyword grammar: its own confidence
                    intent, conf = rec.selected, g["confidence"]
                else:
                    intent = rec.selected if rec.acted else None
                    conf = ans.confidence if ans is not None and ans.confidence is not None else 0.0
                preds = {a.name: a.probability for a in rec.answers if a.type == "predicate"}
            cmd.update(intent=intent, confidence=round(float(conf or 0.0), 3), engine=engine, decision_id=did,
                       predicates=preds)
            self._emit("voice_intent", id=cmd["id"], intent=intent, confidence=cmd["confidence"], engine=engine,
                       decision_id=did, predicates=preds)
            if intent is None or intent == "UNCLEAR" or (conf or 0.0) < INTENT_GATE:
                self._ask(cmd, "I did not catch one action. Say follow, search, go to, look for, hold or return.")
                return
            if intent not in IMPLEMENTED:
                self._reply(cmd, "declined", f"{intent.title()} is not available in this build. I can follow, search, go "
                            "to a place, look for someone, hold or return.")
                return
            self._ground(cmd, intent)

    # -- 3. grounding ----------------------------------------------------------------------------------------------
    def _ground(self, cmd: dict[str, Any], intent: str) -> None:
        t = cmd["transcript"]
        if intent in ("HOLD", "RTL"):
            self._propose(cmd, intent, {})
            return
        if intent == "FIND":
            desc = parse_description(t)
            if desc is None:
                self._ask(cmd, "Look for whom? Describe their clothing, for example 'red shirt, jeans'.")
                return
            self._propose(cmd, "FIND", {"descriptor": desc.text(), "text": t})
            return
        if intent == "FOLLOW":
            r = self._grounding().person(t, self._people())
        else:
            r = self._grounding().place(t)
        cmd["grounding"] = r.view()
        self._emit("voice_grounding", id=cmd["id"], **r.view())
        if r.status == "ask":
            self._ask(cmd, r.question or "Which one?", r.options, intent)
        elif r.status == "none":
            hint = " Say 'look for' with their clothing to have the belief weigh it." if intent == "FOLLOW" else ""
            self._reply(cmd, "not_found", f"I could not ground that: {r.why}.{hint}")
        else:
            self._propose_for(cmd, intent, r.entity, t)  # type: ignore[arg-type]

    def _propose_for(self, cmd: dict[str, Any], intent: str, e: Entity, transcript: str) -> dict[str, Any]:
        if intent == "FOLLOW":
            return self._propose(cmd, "FOLLOW", {"track_id": e.attrs.get("track_id"), "who": e.label})
        if intent == "GOTO":
            return self._propose(cmd, "GOTO", {"target_id": e.id, "label": e.label, "xy": [round(e.xy[0], 1), round(e.xy[1], 1)]})
        if intent == "SEARCH":
            if e.polygon:
                return self._propose(cmd, "SEARCH", {"sector_id": e.id, "label": e.label, "polygon": e.polygon})
            x, y = e.xy
            b = SEARCH_BOX_M
            return self._propose(cmd, "SEARCH", {"around": e.id, "label": f"the area around {e.label}",
                                                 "polygon": [(x - b, y - b), (x + b, y - b), (x + b, y + b), (x - b, y + b)]})
        return self._reply(cmd, "declined", f"{intent.title()} does not take a place.")

    # -- 4. the primitive --------------------------------------------------------------------------------------------
    def _propose(self, cmd: dict[str, Any], primitive: str, params: dict[str, Any]) -> dict[str, Any]:
        p = self.p
        xy = params.get("xy")
        if xy is not None or params.get("polygon"):
            pts = [tuple(xy)] if xy is not None else [tuple(q) for q in params["polygon"]]
            centre = (sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts))
            ok, _, why = p.autonomy.safety.check_viewpoint(centre, [centre] if xy is not None else [], p.cfg.autonomy.altitude_m, [])
            if not ok:
                return self._reply(cmd, "declined", f"The safety supervisor rejects that target: {why}.")
        policy = APPROVAL[primitive]
        needs = policy == "always" or (policy == "live" and (p.mode == "LIVE" or self.require_approval_sim))
        prim = {"id": f"P-{cmd['id']}", "command": cmd["id"], "primitive": primitive, "parameters": params,
                "proposed_by": "human", "approval_status": "pending" if needs else "not_required",
                "execution_status": "not_started", "safety_constraints": CONSTRAINTS[primitive],
                "reason": f"operator command: '{cmd['transcript']}'", "transcript": cmd["transcript"],
                "intent_confidence": cmd.get("confidence"), "engine": cmd.get("engine"), "provenance": "manual",
                "ts_ms": self._now()}
        cmd["primitive"] = prim
        self._emit("voice_primitive", id=cmd["id"], primitive=_public(prim))
        if needs:
            self.pending = prim
            return self._reply(cmd, "awaiting_approval", f"{_say(prim)} Approve or reject.")
        prim["approval_status"] = "not_required"
        return self._execute(cmd, prim)

    def decide(self, pid: str, approve: bool, by: str = "operator") -> dict[str, Any]:
        with self.p.lock:
            prim = self.pending
            if prim is None or prim["id"] != pid:
                raise KeyError(f"no pending primitive {pid}")
            self.pending = None
            cmd = self._cmd(prim["command"]) or {"id": prim["command"], "transcript": prim["transcript"]}
            prim["approval_status"] = "approved" if approve else "rejected"
            prim["decided_by"] = by
            self._emit("voice_primitive_approved" if approve else "voice_primitive_rejected", id=cmd["id"],
                       primitive=_public(prim), by=by)
            if not approve:
                prim["execution_status"] = "cancelled"
                return self._reply(cmd, "rejected", f"Rejected by {by}: nothing moves.")
            return self._execute(cmd, prim)

    def answer(self, option_index: int) -> dict[str, Any]:
        with self.p.lock:
            q = self.question
            if q is None or not (0 <= option_index < len(q["options"])):
                raise KeyError("no such option")
            self.question = None
            cmd = self._cmd(q["cmd"])
            if cmd is None:
                raise KeyError("the question's command is gone")
            return self._propose_for(cmd, q["intent"], q["options"][option_index]["_entity"], cmd["transcript"])

    # -- 5. execution -----------------------------------------------------------------------------------------------
    def _execute(self, cmd: dict[str, Any], prim: dict[str, Any]) -> dict[str, Any]:
        p = self.p
        kind, a = prim["primitive"], prim["parameters"]
        by = f"operator by voice: '{prim['transcript']}'"
        try:
            if kind == "HOLD":
                if p.dispatch_state is not None:
                    p.cancel_dispatch(by="operator (voice)")
                p.set_autonomy_mode("HOLD", "operator (voice)")
                done = "Holding position."
            elif kind == "RTL":
                p.set_autonomy_mode("RTL", "operator (voice)")
                done = "Returning to launch."
            elif kind == "FIND":
                p.set_look_for(a["text"], by="operator (voice)")
                done = f"Looking for {a['descriptor']}: the words weigh the pursuit belief (no flight)."
            elif kind == "FOLLOW":
                if p.dispatch_state is not None:
                    p.cancel_dispatch(by="operator (voice)")
                p.lock_track("subject", a["track_id"], locked_by=by)
                p.set_autonomy_mode("FOLLOW", "operator (voice)")
                done = f"Following {a['who']}."
            elif kind == "GOTO":
                p.goto(tuple(a["xy"]), a["label"], by="operator (voice)")
                done = f"Flying to {a['label']}."
            elif kind == "SEARCH":
                st = p.search_area([tuple(q) for q in a["polygon"]], a["label"], by="operator (voice)")
                done = f"Searching {a['label']}: {st.legs} legs."
            else:
                raise ValueError(kind)
        except Exception as e:  # noqa: BLE001 - a refused command is reported, never half-done silently
            prim["execution_status"] = "failed"
            self._emit("voice_primitive_failed", id=cmd["id"], primitive=_public(prim), error=str(e))
            return self._reply(cmd, "failed", f"Could not do it: {e}")
        prim["execution_status"] = "done"
        self._emit("voice_primitive_executed", id=cmd["id"], primitive=_public(prim), result=done)
        return self._reply(cmd, "done", done)

    # -- read model --------------------------------------------------------------------------------------------------
    def view(self) -> dict[str, Any]:
        q = self.question
        return {"commands": [_public_cmd(c) for c in list(self.cmds)[-6:]],
                "question": {k: v for k, v in q.items() if k != "options"} | {"options": [
                    {k: v for k, v in o.items() if k != "_entity"} for o in q["options"]]} if q else None,
                "pending": _public(self.pending) if self.pending else None,
                "require_approval_sim": self.require_approval_sim,
                "engine": self.p.cfg.decisions.voice_engine,
                "gate": INTENT_GATE}


def _say(prim: dict[str, Any]) -> str:
    a = prim["parameters"]
    return {"FOLLOW": f"Follow {a.get('who')}?", "GOTO": f"Fly to {a.get('label')}?",
            "SEARCH": f"Search {a.get('label')}?"}.get(prim["primitive"], prim["primitive"].title() + "?")


def _public(prim: dict[str, Any] | None) -> dict[str, Any] | None:
    if prim is None:
        return None
    out = dict(prim)
    a = dict(out.get("parameters") or {})
    if a.get("polygon"):
        a["polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in a["polygon"]]
    out["parameters"] = a
    return out


def _public_cmd(c: dict[str, Any]) -> dict[str, Any]:
    return {k: (_public(v) if k == "primitive" else v) for k, v in c.items() if k != "grammar"} | \
        {"grammar_confidence": (c.get("grammar") or {}).get("confidence")}

