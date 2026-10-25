"""Decision router + services.

Router: primary engine with a hard deadline, fallback chain on timeout/error/429, refusal -> no optional action,
shadow engines in parallel for the comparison panel, deterministic rules baseline computed for every job so the
"rule vs AI" disagreement is measured, not asserted. Every call is logged with engine, latency, probabilities and
whether the answer was acted on.

Services gate submissions (one in flight per task, per-task rate cap, scene-fingerprint dedupe) and deliver results
back to the pipeline either asynchronously (server) or at simulated latency in sim time (headless runner).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..faults import FaultInjector
from ..ids import IdGen
from ..schemas import DecisionRecord, DecisionStatus, EngineStatus
from .engines import (BaseEngine, EngineError, MockEngine, OpenAIDecisionsEngine, RulesEngine, StrandsEngine,
                      TypeSafeEngine, rule_answers)
from .tasks import PRIMARY_QUESTION, prepare
from .types import DecisionJob, EngineResult

log = logging.getLogger(__name__)


class _Stats:
    def __init__(self) -> None:
        self.lat: deque[float] = deque(maxlen=100)
        self.calls = self.errors = self.timeouts = self.refusals = 0

    def pct(self, q: float) -> float | None:
        if not self.lat:
            return None
        s = sorted(self.lat)
        return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 1)


class DecisionRouter:
    def __init__(self, cfg: Config, faults: FaultInjector, mode: str) -> None:
        self.cfg = cfg
        self.mode = mode
        dc, s = cfg.decisions, cfg.secrets
        imgs = dc.allow_images and cfg.privacy.send_images_to_cloud
        strands_url = (s.strands_url.rstrip("/") + "/v1/systemone") if s.strands_url else dc.strands_url
        self.engines: dict[str, BaseEngine] = {
            "openai": OpenAIDecisionsEngine(s.openai_api_key, dc.openai_model, s.openai_base_url or dc.openai_base_url,
                                            faults, imgs),
            "typesafe": TypeSafeEngine(s.typesafe_api_key, dc.typesafe_model, dc.typesafe_url, faults, imgs),
            "strands": StrandsEngine(strands_url, faults),
            "mock": MockEngine(faults),
            "rules": RulesEngine(faults),
        }
        self.primary_cfg = dc.primary
        self.shadow_cfg = list(dc.shadow)
        self.stats: dict[str, _Stats] = defaultdict(_Stats)
        self.ids = IdGen("d")
        self.records: deque[DecisionRecord] = deque(maxlen=400)
        self.calls_total = self.acted_total = self.disagreements = 0
        self.consecutive_failures = 0
        self.ai_paused = False
        self.pause_reason: str | None = None
        self.log_path: Path | None = Path(dc.log_path) if dc.log_path else None
        self.session = 0  # bumped on every new session; answers carrying an older session number are dropped
        self.session_model_calls = self.session_tokens = 0
        self.dropped_stale = self.rejected_invalid = 0

    def new_session(self) -> None:
        self.session += 1
        self.session_model_calls = self.session_tokens = 0

    def budget_exhausted(self) -> bool:
        dc = self.cfg.decisions
        return self.session_model_calls >= dc.max_model_calls_per_session or self.session_tokens >= dc.max_tokens_per_session

    def drop_if_stale(self, job: DecisionJob, records: list[DecisionRecord], now_ms: float) -> str | None:
        """Freshness rule: an answer from an earlier session, or older than ``stale_after_s``, is logged, never acted on."""
        age = now_ms - job.created_ms
        limit = self.cfg.decisions.stale_after_s * 1000.0
        why = None
        if job.context.get("session", self.session) != self.session:
            why = "answer from an earlier session dropped"
        elif age > limit:
            why = f"answer {age:.0f} ms old (limit {limit:.0f} ms) dropped"
        if why is None:
            return None
        self.dropped_stale += 1
        for r in records:
            if r.acted and not r.shadow:
                self.acted_total -= 1
            r.acted = False
            r.reason = f"{why}; {r.reason}"
            r.facts["_dropped"] = True
        return why

    # ---------------------------------------------------------------------------------------------------------
    def set_engines(self, primary: str | None = None, shadow: list[str] | None = None) -> None:
        if primary is not None:
            if primary not in self.engines:
                raise ValueError(f"unknown engine {primary}")
            self.primary_cfg = primary
        if shadow is not None:
            self.shadow_cfg = [s for s in shadow if s in self.engines]

    def _primary_for(self, job: DecisionJob) -> str:
        """The engine for one job: a job may name one (``context["engine"]``, e.g. rules for pursuit routing when the
        configuration keeps flight-related choices off the model); otherwise the configured primary."""
        forced = job.context.get("engine")
        if forced in self.engines:
            return str(forced)
        return self.resolve_primary()

    def resolve_primary(self) -> str:
        p = self.primary_cfg
        if p not in ("rules", "mock") and self.budget_exhausted():
            return "rules"
        eng = self.engines.get(p)
        if eng is not None and eng.available()[0]:
            return p
        if self.cfg.decisions.allow_mock and self.mode == "SIMULATED" and p in ("openai", "typesafe", "mock"):
            return "mock"
        return "rules"

    async def refresh(self) -> None:
        st = self.engines["strands"]
        if isinstance(st, StrandsEngine):
            await st.probe()
        oa = self.engines["openai"]
        if isinstance(oa, OpenAIDecisionsEngine):
            await oa.warmup()  # keeps the pooled connection warm between decisions

    # ---------------------------------------------------------------------------------------------------------
    async def _call(self, name: str, job: DecisionJob) -> tuple[EngineResult | None, str, str | None]:
        eng = self.engines[name]
        dl = self.cfg.decisions.deadline_ms
        st = self.stats[name]
        st.calls += 1
        try:
            res = await asyncio.wait_for(eng.decide(job, dl), timeout=dl / 1000.0)
        except asyncio.TimeoutError:
            st.timeouts += 1
            return None, "timeout", f"no answer within {dl:.0f} ms"
        except EngineError as e:
            if e.status == "timeout":
                st.timeouts += 1
            else:
                st.errors += 1
            return None, e.status, str(e)
        except Exception as e:  # noqa: BLE001 - engine bugs must not break the pipeline
            st.errors += 1
            log.exception("engine %s crashed", name)
            return None, "error", f"{type(e).__name__}: {e}"
        st.lat.append(res.latency_ms)
        if res.status == "refusal":
            st.refusals += 1
        if not eng.mock and name != "rules":
            self.session_model_calls += 1
            u = res.usage or {}
            self.session_tokens += int(u.get("total_tokens") or (u.get("input_tokens", 0) + u.get("output_tokens", 0)) or 0)
        return res, res.status, res.error

    @staticmethod
    def _no_choice_left(job: DecisionJob) -> bool:
        """After unsafe options are removed, a choice with one option left needs no model call."""
        return any(q.kind == "choice" and len(q.options) < 2 for q in job.questions)

    async def run(self, job: DecisionJob, now_ms: Callable[[], float]) -> list[DecisionRecord]:
        prepare(job)
        if self._no_choice_left(job):
            res = self.engines["rules"].decide_sync(job)  # type: ignore[attr-defined]
            rec = self._record(job, res, "rules", now_ms(), note="one option left after removing unsafe options: no model call")
            self.records.append(rec)
            self._log(rec)
            return [rec]
        primary = self._primary_for(job)
        shadow_names = [] if "engine" in job.context else \
            [s for s in self.shadow_cfg if s != primary and self.engines[s].available()[0]]
        shadow_tasks = {s: asyncio.create_task(self._call(s, job)) for s in shadow_names}
        req_ms = now_ms()
        res, status, err = await self._call(primary, job)
        chain_note = ""
        used = primary
        if res is None:
            self._note_failure(primary, status, err)
            for fb in self.cfg.decisions.fallback_chain:
                if fb == primary or not self.engines[fb].available()[0]:
                    continue
                r2, s2, e2 = await self._call(fb, job)
                if r2 is not None and r2.status == "success":
                    res, used = r2, fb
                    chain_note = f"{primary} {status} ({err}) -> {fb}"
                    break
            if res is None:
                res = self.engines["rules"].decide_sync(job)  # type: ignore[attr-defined]
                used = "rules"
                chain_note = f"{primary} {status} ({err}) -> rules"
        elif res.status == "success":
            self._note_success()
        rec = self._record(job, res, used, req_ms, primary_status=status if used != primary else None, note=chain_note)
        records = [rec]
        for s, t in shadow_tasks.items():
            try:
                sres, sst, serr = await asyncio.wait_for(t, timeout=self.cfg.decisions.deadline_ms / 1000.0 * 2)
            except asyncio.TimeoutError:
                sres, sst, serr = None, "timeout", "shadow timeout"
            if sres is None:
                sres = EngineResult(s, self.engines[s].model, "timeout" if sst == "timeout" else "error", 0.0, error=serr)
            srec = self._record(job, sres, s, req_ms, shadow=True)
            records.append(srec)
        for r in records:
            self.records.append(r)
            self._log(r)
        return records

    def run_sync(self, job: DecisionJob, now_ms: float, cloud_fault: str | None = None) -> tuple[list[DecisionRecord], float]:
        """Headless variant: mock/rules engines only. Returns (records, simulated latency ms)."""
        prepare(job)
        primary = self._primary_for(job)
        if primary not in ("mock", "rules"):
            primary = "mock" if self.cfg.decisions.allow_mock else "rules"
        if primary == "mock" and cloud_fault:
            lat = {"api_timeout": self.cfg.decisions.deadline_ms, "api_429": 60.0, "network_loss": 20.0,
                   "api_refusal": 200.0}[cloud_fault]
            if cloud_fault == "api_refusal":
                res = EngineResult("mock", "MOCK (no API key)", "refusal", lat, mock=True, error="refusal (injected)")
                rec = self._record(job, res, "mock", now_ms)
            else:
                self._note_failure("mock", "timeout" if cloud_fault == "api_timeout" else "error", cloud_fault)
                res = self.engines["rules"].decide_sync(job)  # type: ignore[attr-defined]
                rec = self._record(job, res, "rules", now_ms, primary_status="timeout" if cloud_fault == "api_timeout" else "error",
                                   note=f"mock {cloud_fault} (injected) -> rules")
            self.records.append(rec)
            return [rec], lat
        if primary == "mock":
            res = self.engines["mock"].sample(job)  # type: ignore[attr-defined]
            self.stats["mock"].calls += 1
            self.stats["mock"].lat.append(res.latency_ms)
            self._note_success()
        else:
            res = self.engines["rules"].decide_sync(job)  # type: ignore[attr-defined]
        rec = self._record(job, res, primary, now_ms)
        self.records.append(rec)
        self._log(rec)
        return [rec], res.latency_ms

    # ---------------------------------------------------------------------------------------------------------
    def _note_failure(self, name: str, status: str, err: str | None) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= 1 and name != "rules":
            self.ai_paused = True
            self.pause_reason = f"{name}: {status}" + (f" ({err})" if err else "")

    def _note_success(self) -> None:
        self.consecutive_failures = 0
        self.ai_paused = False
        self.pause_reason = None

    def _record(self, job: DecisionJob, res: EngineResult, engine: str, req_ms: float, shadow: bool = False,
                primary_status: str | None = None, note: str = "") -> DecisionRecord:
        dc = self.cfg.decisions
        qname = PRIMARY_QUESTION[job.task]
        ans = res.answer(qname)
        selected = ans.choice if ans is not None else None
        conf = ans.confidence if ans is not None else None
        min_conf = dc.identity_min_confidence if job.task in ("D2_identity", "D3_describe") else dc.min_confidence
        min_conf = dc.min_confidence_by_task.get(job.task, min_conf)
        if res.status == "refusal":
            acted, reason, status = False, "model refusal -> no optional action", "refusal"
        elif res.status != "success" or selected is None:
            acted, reason, status = False, res.error or res.status, res.status if res.status in ("timeout", "error") else "error"
        elif engine == "rules":
            acted, reason, status = True, "deterministic rules", "fallback" if primary_status else "success"
        elif conf is None or conf >= min_conf:
            acted, reason, status = True, f"confidence {conf:.2f} >= {min_conf}" if conf is not None else "ok", "success"
        else:
            acted, reason, status = False, f"confidence {conf:.2f} < {min_conf}: not acted", "success"
        valid_values = {o.value for q in job.questions if q.name == qname for o in q.options}
        if acted and selected not in valid_values:
            acted, reason = False, f"invalid choice {selected!r} rejected"
            self.rejected_invalid += 1
        elif acted and engine != "rules" and ans is not None and ans.probabilities:
            bad = probabilities_problem(ans.probabilities, valid_values, selected, dc.probability_sum_tolerance)
            if bad:
                acted, reason = False, f"invalid probabilities rejected: {bad}"
                self.rejected_invalid += 1
        if note:
            reason = f"{note}; {reason}"
        baseline = None
        if not shadow:
            base = rule_answers(job)
            b = next((a for a in base if a.name == qname), None)
            baseline = b.choice if b else None
            self.calls_total += 1
            if acted:
                self.acted_total += 1
            if engine != "rules" and baseline is not None and selected is not None and baseline != selected:
                self.disagreements += 1
        facts = dict(job.facts)
        if baseline is not None:
            facts["_rules_baseline"] = baseline
        return DecisionRecord(
            decision_id=self.ids(), task=job.task, engine=engine, model=res.model, status=status,  # type: ignore[arg-type]
            latency_ms=round(res.latency_ms, 1), request_ts_ms=req_ms, event_ids=job.event_ids, facts=facts,
            answers=res.answers, selected=selected, acted=acted, reason=reason, shadow=shadow, mock=res.mock,
            image_used=bool(job.image_jpeg) and dc.allow_images, error=res.error, usage=res.usage)

    def _log(self, rec: DecisionRecord) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(rec.model_dump_json() + "\n")
        except OSError:
            pass

    def mode_label(self) -> str:
        active = self.resolve_primary()
        if active == "rules":
            if self.primary_cfg not in ("rules", "mock") and self.budget_exhausted():
                return (f"rules only (session budget used: {self.session_model_calls} calls, "
                        f"{self.session_tokens} tokens)")
            _, detail = self.engines[self.primary_cfg].available() if self.primary_cfg in self.engines else (False, "")
            if self.primary_cfg == "rules":
                return "rules only (selected)"
            return "rules only (no API key)" if "not set" in detail else f"rules only ({detail})"
        if active == "mock":
            return "MOCK model (no API key), rules baseline on every call"
        return f"{active} ({self.engines[active].model}), rules baseline on every call"

    def status(self) -> DecisionStatus:
        engines = []
        for name, eng in self.engines.items():
            ok, detail = eng.available()
            st = self.stats.get(name, _Stats())
            engines.append(EngineStatus(name=name, available=ok, mock=eng.mock, detail=detail, p50_ms=st.pct(0.5),
                                        p95_ms=st.pct(0.95), calls=st.calls, errors=st.errors, timeouts=st.timeouts,
                                        refusals=st.refusals))
        return DecisionStatus(primary=self.primary_cfg, active=self.resolve_primary(), ai_paused=self.ai_paused,
                              pause_reason=self.pause_reason, engines=engines, calls_total=self.calls_total,
                              acted_total=self.acted_total, disagreements=self.disagreements,
                              mode_label=self.mode_label(), session=self.session,
                              session_model_calls=self.session_model_calls, session_tokens=self.session_tokens,
                              budget_exhausted=self.budget_exhausted(), dropped_stale=self.dropped_stale,
                              rejected_invalid=self.rejected_invalid)


def probabilities_problem(probs: dict[str, float], options: set[str], selected: str | None, tol: float) -> str | None:
    """Validation rule: probabilities only over the offered options, each in [0, 1], summing to about 1, and the
    selected option must be the most probable one."""
    extra = set(probs) - options
    if extra:
        return f"values outside the options {sorted(extra)}"
    if any(not (0.0 <= p <= 1.0) for p in probs.values()):
        return "a probability outside [0, 1]"
    total = sum(probs.values())
    if abs(total - 1.0) > tol:
        return f"probabilities sum to {total:.2f}"
    if selected is not None and probs.get(selected, 0.0) + 1e-9 < max(probs.values()):
        return f"{selected!r} is not the most probable option"
    return None


def fingerprint(facts: dict[str, Any]) -> str:
    def rnd(v: Any) -> Any:
        if isinstance(v, float):
            return round(v, 1)
        if isinstance(v, dict):
            return {k: rnd(x) for k, x in v.items() if not k.startswith("_") and k not in ("age_ms", "time_since_loss_s")}
        if isinstance(v, list):
            return [rnd(x) for x in v]
        return v
    return hashlib.sha1(json.dumps(rnd(facts), sort_keys=True, default=str).encode()).hexdigest()[:16]


class DecisionService:
    """Async (server) service: gates jobs, runs the router, hands records back via ``on_result``."""

    def __init__(self, router: DecisionRouter, on_result: Callable[[DecisionJob, list[DecisionRecord]], None],
                 now_ms: Callable[[], float]) -> None:
        self.router = router
        self.on_result = on_result
        self.now_ms = now_ms
        self.in_flight: dict[str, asyncio.Task] = {}
        self.last_call: dict[str, float] = {}
        self.last_fp: dict[str, tuple[str, float]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def reset(self) -> None:
        """New session: the clock restarts, so rate limits and dedupe from the old session must not carry over."""
        self.router.new_session()
        self.in_flight.clear()
        self.last_call.clear()
        self.last_fp.clear()

    def gate(self, job: DecisionJob) -> bool:
        dc = self.router.cfg.decisions
        now = self.now_ms()
        if now < self.last_call.get(job.task, -1e12):  # clock went backwards (scenario restart or loop)
            self.reset()
        t = self.in_flight.get(job.task)
        if t is not None and not t.done():
            return False
        if job.task != "D5_priority" and now - self.last_call.get(job.task, -1e12) < 1000.0 / dc.max_hz_per_task:
            return False
        fp = fingerprint(job.facts)
        last = self.last_fp.get(job.task)
        if last and last[0] == fp and now - last[1] < dc.stale_after_s * 1000.0:
            return False
        self.last_call[job.task] = now
        self.last_fp[job.task] = (fp, now)
        return True

    def submit(self, job: DecisionJob) -> bool:
        if self.loop is None or self.loop.is_closed() or not self.gate(job):
            return False
        job.created_ms = self.now_ms()
        job.context["session"] = self.router.session

        async def _run() -> None:
            recs = await self.router.run(job, self.now_ms)
            self.router.drop_if_stale(job, recs, self.now_ms())
            self.on_result(job, recs)

        self.in_flight[job.task] = asyncio.run_coroutine_threadsafe(_run(), self.loop)  # type: ignore[assignment]
        return True


class SimTimeDecisionService(DecisionService):
    """Headless service: answers computed immediately (mock/rules) but delivered after the simulated latency,
    measured in simulation time, so evaluation reflects decision delay without wall-clock coupling.

    With ``live_engines`` a real engine (key present) is called for real - on one background event loop, since the
    engines keep their HTTP clients - and its measured wall-clock latency is replayed in simulation time."""

    def __init__(self, router: DecisionRouter, on_result, now_ms, faults: FaultInjector, live_engines: bool = False) -> None:
        super().__init__(router, on_result, now_ms)
        self.faults = faults
        self.queue: list[tuple[float, DecisionJob, list[DecisionRecord]]] = []
        self.busy: dict[str, float] = {}
        self._live_loop: asyncio.AbstractEventLoop | None = None
        if live_engines:
            self._live_loop = asyncio.new_event_loop()
            threading.Thread(target=self._live_loop.run_forever, name="decisions-live", daemon=True).start()
            asyncio.run_coroutine_threadsafe(router.refresh(), self._live_loop).result(timeout=15)  # warm connections

    def reset(self) -> None:
        super().reset()
        self.queue.clear()
        self.busy.clear()

    def submit(self, job: DecisionJob) -> bool:
        now = self.now_ms()
        if now < self.last_call.get(job.task, -1e12):  # clock went backwards (scenario restart or loop)
            self.reset()
        if self.busy.get(job.task, -1) > now:
            return False
        dc = self.router.cfg.decisions
        if job.task != "D5_priority" and now - self.last_call.get(job.task, -1e12) < 1000.0 / dc.max_hz_per_task:
            return False
        fp = fingerprint(job.facts)
        last = self.last_fp.get(job.task)
        if last and last[0] == fp and now - last[1] < dc.stale_after_s * 1000.0:
            return False
        self.last_call[job.task] = now
        self.last_fp[job.task] = (fp, now)
        job.created_ms = now
        job.context["session"] = self.router.session
        if self._live_loop is not None and self.router.resolve_primary() not in ("mock", "rules"):
            t0 = time.perf_counter()
            recs = asyncio.run_coroutine_threadsafe(self.router.run(job, lambda: now), self._live_loop).result(timeout=60)
            lat = (time.perf_counter() - t0) * 1000.0  # true latency, timeouts and fallbacks included
        else:
            recs, lat = self.router.run_sync(job, now, self.faults.cloud_fault())
            lat = min(lat, dc.deadline_ms) if recs and recs[0].engine == "rules" else lat
        for r in recs:
            r.latency_ms = round(lat, 1)
        self.queue.append((now + lat, job, recs))
        self.busy[job.task] = now + lat
        return True

    def poll(self) -> None:
        now = self.now_ms()
        due = [q for q in self.queue if q[0] <= now]
        self.queue = [q for q in self.queue if q[0] > now]
        for _, job, recs in due:
            self.router.drop_if_stale(job, recs, now)
            self.on_result(job, recs)
