"""Headless, deterministic scenario runner: same SimulationDriver + Pipeline as the server, stepped synchronously on
simulation time (no pacing, no frame skipping). Decisions use the mock/rules engines with simulated latency in sim
time. Produces a ground-truth evaluation report labelled ``synthetic``."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..clock import SimClock
from ..config import Config, load_config
from ..decisions.router import DecisionRouter, SimTimeDecisionService
from ..evaluation.metrics import Metrics
from ..faults import FaultInjector
from ..ids import session_id
from ..pipeline import Pipeline
from ..schemas import Zone
from ..sim.driver import SimulationDriver


class OperatorEmulator:
    """Executes scripted operator actions by *clicking* on the named actor in the latest frame, exactly like a
    human would on the dashboard (the click goes through the same track hit-test)."""

    def __init__(self, pipeline: Pipeline, sim: SimulationDriver) -> None:
        self.p = pipeline
        self.sim = sim
        self.pending: list[tuple[float, Any]] = []
        self.log: list[dict] = []
        self.watch: str | None = None  # actor the scripted operator keeps checking unverified re-bindings against
        self._unverified_since: float | None = None
        self.confirm_delay_ms = 1200.0  # the scripted operator's reaction time to "confirm or re-designate"

    def add(self, t_ms: float, actions: list) -> None:
        self.pending.extend((t_ms, a) for a in actions)

    def check_rebind(self, t_ms: float) -> None:
        """A scripted operator watching the video: when the subject lock re-binds without confirmation, confirm it if
        it is on the right person, re-designate the right person if they are visible, otherwise say "not them" and
        reject the re-binding (all logged)."""
        if self.watch is None:
            return
        lk = self.p.locks["subject"]
        if not (lk.active and lk.state == "LOCKED" and not lk.verified):
            self._unverified_since = None
            return
        if self._unverified_since is None:
            self._unverified_since = t_ms
        if t_ms - self._unverified_since < self.confirm_delay_ms:  # a person needs a moment to look
            return
        gt = self.sim.last_gt.get("actors", {}).get(self.watch) or {}
        box = next((t.box for t in self.p.raw_tracks if t.track_id == lk.track_id), None)
        if box is None:
            return
        from .gt_eval import _iou

        if gt.get("box_xyxy") and _iou(box, gt["box_xyxy"]) >= 0.3:
            try:
                self.p.confirm_candidate(lk.track_id)
                self.log.append({"t_ms": t_ms, "action": "confirm_rebind", "track": lk.track_id, "actor": self.watch})
            except KeyError:
                pass
        elif gt.get("box_xyxy") and gt.get("visibility", 0) > 0.3:
            b = gt["box_xyxy"]
            tid = self.p.click((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            if tid and tid != lk.track_id:
                try:
                    self.p.lock_track("subject", tid, locked_by="scripted operator (re-designated)")
                    self.log.append({"t_ms": t_ms, "action": "redesignate", "track": tid, "actor": self.watch})
                except Exception:  # noqa: BLE001
                    pass
        else:  # the re-bound person is someone else and the subject is out of sight: "not them"
            try:
                self.p.reject_rebind(by="scripted operator")
                self.log.append({"t_ms": t_ms, "action": "reject_rebind", "track": lk.track_id, "actor": self.watch})
            except ValueError:
                pass

    def run(self, t_ms: float) -> None:
        keep = []
        for t_req, act in self.pending:
            done = False
            if act.action in ("lock_subject", "lock_responder"):
                gt = self.sim.last_gt.get("actors", {}).get(act.arg)
                if gt and gt.get("box_xyxy") and gt.get("visibility", 0) > 0.3:
                    b = gt["box_xyxy"]
                    tid = self.p.click((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                    if tid:
                        role = "subject" if act.action == "lock_subject" else "responder"
                        try:
                            self.p.lock_track(role, tid, locked_by="scripted operator (demo)")
                            done = True
                            self.log.append({"t_ms": t_ms, "action": act.action, "track": tid, "actor": act.arg})
                        except Exception:  # noqa: BLE001
                            pass
            elif act.action == "autonomy":
                try:
                    self.p.set_autonomy_mode(str(act.arg), "scripted operator (demo)")
                except ValueError:
                    pass
                done = True
            elif act.action == "describe":
                done = self.p.describe(str(act.arg))
            elif act.action == "operator_watch":
                self.watch = str(act.arg)
                done = True
            elif act.action == "look_for":
                self.p.set_look_for(str(act.arg), "scripted operator")
                self.log.append({"t_ms": t_ms, "action": "look_for", "text": act.arg})
                done = True
            elif act.action == "voice":  # an operator command, as a transcript (the speech-to-text output)
                r = self.p.voice.handle(str(act.arg), source="scripted operator", by="scripted operator")
                self.log.append({"t_ms": t_ms, "action": "voice", "text": act.arg, "id": r["id"]})
                done = True
            elif act.action == "approve_voice":  # the scripted operator approves (or rejects) what is pending
                pend = self.p.voice.pending
                if pend is not None:
                    self.p.voice.decide(pend["id"], approve=act.arg != "reject", by="scripted operator")
                    self.log.append({"t_ms": t_ms, "action": "approve_voice", "primitive": pend["primitive"],
                                     "approved": act.arg != "reject"})
                    done = True
            elif act.action == "answer_voice":  # the scripted operator answers the question asked
                if self.p.voice.question is not None:
                    self.p.voice.answer(int(act.arg))
                    self.log.append({"t_ms": t_ms, "action": "answer_voice", "option": act.arg})
                    done = True
            elif act.action == "dispatch":  # arg: {description, last_seen_xy, radius_m, altitude_m}
                try:
                    self.p.dispatch(**dict(act.arg), by="scripted operator (demo)")
                    self.log.append({"t_ms": t_ms, "action": "dispatch", **dict(act.arg)})
                    done = True
                except Exception as e:  # noqa: BLE001 - e.g. no telemetry yet: retried next step
                    self.log.append({"t_ms": t_ms, "action": "dispatch_failed", "error": str(e)}) if t_ms - t_req > 10000 else None
            elif act.action == "inject_conflict":
                c = next((c for c in self.sim.scenario.conflicts if c.id == act.arg), None)
                if c is not None:
                    self.p.inject_scripted_events(c.id, [vars(e) for e in c.events], t_ms)
                    self.log.append({"t_ms": t_ms, "action": "inject_conflict", "conflict": c.id})
                done = True
            if not done and t_ms - t_req < 15000.0:
                keep.append((t_req, act))
        self.pending = keep


@dataclass
class StepRecord:
    t_ms: float
    gt: dict[str, Any]
    tracks: list[dict]
    locks: dict[str, Any]
    calib: dict[str, Any]
    kin: dict[str, Any]
    responder: dict[str, Any]
    autonomy: dict[str, Any]
    phone: dict[str, Any] | None = None  # latest phone frame: vision boxes/ranges, their GT, cross-view matches
    memory: list[dict] = field(default_factory=list)  # object memory snapshot (persistent ids, hidden states)
    pursuit: dict[str, Any] | None = None  # the street-graph belief while the subject is lost (hypothesis weights)


@dataclass
class RunResult:
    scenario: str
    duration_s: float
    wall_s: float
    steps: list[StepRecord] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    operator_log: list[dict] = field(default_factory=list)
    registration: dict[str, Any] = field(default_factory=dict)
    engine: dict[str, Any] = field(default_factory=dict)
    voice: list[dict] = field(default_factory=list)  # operator commands: transcript, intent, primitive, approval
    rescue: dict[str, Any] | None = None
    crew_routes: list[dict] = field(default_factory=list)  # the live crew planner's routes: {ts_ms, depart_s, route}


class HeadlessRunner:
    def __init__(self, cfg: Config | None, scenario_id: str, seed: int = 0, engine: str | None = None) -> None:
        self.cfg = cfg or load_config()
        self.cfg.session.mode = "SIMULATED"
        self.faults = FaultInjector()
        self.clock = SimClock()
        self.metrics = Metrics()
        self.sim = SimulationDriver(self.cfg, self.faults, scenario_id, seed)
        self.router = DecisionRouter(self.cfg, self.faults, "SIMULATED")
        self.pipeline = Pipeline(self.cfg, self.clock, "SIMULATED", self.faults, self.metrics, self.router,
                                 self.sim.bridge, session_id())
        self.pipeline.zones = [Zone(zone_id=z.zone_id, name=z.name, polygon_xy_m=z.polygon, kind="task")  # type: ignore[arg-type]
                               for z in self.sim.world.zones]
        if engine:
            self.router.set_engines(primary=engine)
        self.engine_requested = engine
        # without --engine, headless runs use the seeded mock (or rules) whatever keys are configured
        self.engine_used = self.router.resolve_primary() if engine else ("mock" if self.cfg.decisions.allow_mock else "rules")
        self.decisions = SimTimeDecisionService(self.router, self.pipeline.apply_decision, self.clock.now_ms, self.faults,
                                                live_engines=bool(engine))
        self.pipeline.decisions = self.decisions
        self.pipeline.map_provider = self.sim.map_data
        from ..voice.grounding import world_entities
        self.pipeline.voice.entities_provider = lambda: world_entities(self.sim.world, self.sim.map_data())
        self.pipeline.crew.problem_provider = self.sim.crew_problem
        self.pipeline.rescue.sim_provider = lambda: self.sim
        self.sim.rescue = self.pipeline.rescue
        self.pipeline.detector_override = self.sim.scenario.detector
        self.pipeline.autonomy.obstacle_sensor = self.sim.obstacle_scene if self.sim.scenario.stack == "jev-x2" else None
        self.operator = OperatorEmulator(self.pipeline, self.sim)
        self._phone_gt: tuple[int, list[dict]] | None = None

    def run(self, duration_s: float | None = None, record_every_ms: float = 100.0,
            on_step: Callable[[HeadlessRunner, float], None] | None = None,
            stop_when: Callable[[HeadlessRunner, float], bool] | None = None) -> RunResult:
        sim, p = self.sim, self.pipeline
        dur = duration_s or sim.scenario.duration_s
        dt = 1.0 / sim.WORLD_HZ
        res = RunResult(sim.scenario.id, dur, 0.0)
        last_auto = last_tick = last_rec = -1e12
        t_wall = time.perf_counter()
        while sim.t_s < dur:
            out = sim.step(dt, True)
            if out.restarted:
                break
            t = out.t_ms
            self.clock.set_ms(t)
            self.operator.add(t, out.actions)
            if out.telemetry:
                p.process_telemetry(out.telemetry)
            if out.phone:
                p.process_phone(out.phone)
                self._phone_gt = (out.phone.observation.seq, out.phone_gt or [])
            if out.dropped_frame:
                p.note_dropped("drone")
            if out.drone_frame:
                p.process_drone_frame(out.drone_frame)
            if self.operator.pending:
                self.operator.run(t)
            self.operator.check_rebind(t)
            if t - last_auto >= 50.0 - 1:
                last_auto = t
                p.autonomy_tick(t)
            if t - last_tick >= 100.0 - 1:
                last_tick = t
                p.sim_status = {**sim.status(), "speed": 0.0, "paused": False, "gt_available": True}
                p.tick(t)
            if t - last_rec >= record_every_ms - 1:
                last_rec = t
                res.steps.append(self._record(t))
            if on_step:
                on_step(self, t)
            if stop_when and stop_when(self, t):
                res.duration_s = sim.t_s
                break
        res.wall_s = time.perf_counter() - t_wall
        res.events = [e.model_dump() for e in p.events]
        res.alerts = [a.model_dump() for a in p.arbiter.history]
        res.decisions = [d.model_dump() for d in p.decision_log]
        res.metrics = self.metrics.snapshot(self.clock.now_ms())
        res.operator_log = self.operator.log
        res.voice = [dict(c) for c in p.voice.cmds]
        res.crew_routes = list(p.crew.history)
        res.rescue = p.rescue.view()
        res.registration = p.registration.status.model_dump()
        res.engine = {"requested": self.engine_requested, "used": self.engine_used,
                      "note": None if not self.engine_requested or self.engine_used == self.engine_requested
                      else f"{self.engine_requested} not available (no key / not running): {self.engine_used} used"}
        return res

    def _record(self, t: float) -> StepRecord:
        p = self.pipeline
        sim = self.sim
        tracks = [{"id": tr.track_id, "xy": (p.world_kf[tr.track_id].position if tr.track_id in p.world_kf else None),
                   "box": tr.measured_box if (tr.matched_now and tr.measured_box) else tr.box, "state": tr.state,
                   "visible": tr.matched_now} for tr in p.raw_tracks]
        gt = {"actors": sim.last_gt.get("actors", {}), "states": sim.gt_states(), "drone_xy": sim.drone.true_xy().tolist(),
              "drone_alt": round(float(sim.drone.pos[2]), 2), "drone_heading": round(float(sim.drone.heading), 1)}
        with p.lock:
            locks = {k: {"state": v.state, "track_id": v.track_id, "xy": v.kf.position if v.kf else None,
                         "verified": v.verified} for k, v in p.locks.items()}
            kin = p.kin_state.model_dump()
            resp = p.responder.model_dump()
        phone = None
        obs = p.phone_obs
        if obs is not None and self._phone_gt is not None and self._phone_gt[0] == obs.seq and obs.vision_boxes is not None:
            phone = {"seq": obs.seq, "orientation": obs.image_orientation, "boxes": obs.vision_boxes,
                     "ranges": obs.vision_ranges_m, "gt": self._phone_gt[1], "cross_view": list(p._cross_view)}
        with p.lock:
            mem = [{"oid": o.oid, "state": o.state, "track_id": o.track_id, "link": o.link,
                    "xy": (float(o.xy[0]), float(o.xy[1]))} for o in p.memory.objects.values()]
            b = p.belief
            pursuit = None
            if b is not None:
                pursuit = {"weights": b.hyp_weights(), "labels": {h.hid: h.label for h in b.hyps},
                           "objects": {h.hid: h.object_id for h in b.hyps},
                           "route": (p.pursuit_route or {}).get("candidate_id")}
        return StepRecord(t, gt, tracks, locks, p.calib_status.model_dump(include={"valid", "source", "error_bound_m",
                                                                                   "holdout_rmse_m"}),
                          kin, resp, {**p.autonomy.state().model_dump(include={"mode", "fsm", "primitive", "reflex", "maneuver",
                                                                               "scene_ahead_m"}),
                                      "dispatch": p.dispatch_state.model_dump() if p.dispatch_state else None},
                          phone, mem, pursuit)
