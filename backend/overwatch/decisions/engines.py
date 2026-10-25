"""Decision engine adapters.

Wire formats follow the vendor docs read on  (see docs/decision_engines.md):
* OpenAI Decisions: ``POST {base}/decisions`` with ``model``, ``input`` (string or messages with ``input_text`` /
  ``input_json`` / ``input_image`` parts) and ``questions`` (list of ``{type, name, instructions, choices|levels}``);
  response ``answers`` is a list matched by ``name`` (choice -> ``choice``, ``confidence``, ``probabilities``
  as ``[{value, probability}]``; predicate -> ``probability``; score -> ``score``, ``confidence``,
  ``probabilities`` as ``[{value, label, probability}]``; or ``refusal``).
* TypeSafe Jev: ``POST https://api.typesafe.ai/v1/systemone`` with ``model``, ``state``, ``questions`` (a map
  name -> ``{type: noul|choice|score, instructions, criteria}``), optional ``assets`` + header
  ``TypeSafe-Danger-Zone-Images: true``; response ``answers`` is a map by name.
* Strands Decider: local ``/v1/systemone`` with TypeSafe-shaped questions; text-only by default.
"""

from __future__ import annotations

import asyncio
import base64
import math
import time
import uuid
from typing import Any

import httpx
import numpy as np

from ..faults import FaultInjector
from ..schemas import DecisionAnswer
from .tasks import RULES
from .types import PREAMBLE, DecisionJob, EngineResult, Question


class EngineError(Exception):
    def __init__(self, status: str, msg: str) -> None:
        super().__init__(msg)
        self.status = status


class BaseEngine:
    name = "base"
    model: str | None = None
    mock = False
    cloud = False

    def __init__(self, faults: FaultInjector | None = None) -> None:
        self.faults = faults

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:  # pragma: no cover - interface
        raise NotImplementedError

    async def _injected(self, deadline_ms: float) -> EngineResult | None:
        """Apply cloud fault injection. Returns a result to short-circuit with, or None to proceed."""
        if not (self.cloud and self.faults):
            return None
        f = self.faults.cloud_fault()
        if f == "network_loss":
            await asyncio.sleep(0.02)
            raise EngineError("error", "network unreachable (injected)")
        if f == "api_timeout":
            await asyncio.sleep(deadline_ms / 1000.0 + 0.25)
            raise EngineError("timeout", "request timed out (injected)")
        if f == "api_429":
            await asyncio.sleep(0.06)
            raise EngineError("error", "HTTP 429 rate limited (injected)")
        if f == "api_refusal":
            await asyncio.sleep(0.2)
            return EngineResult(self.name, self.model, "refusal", 200.0, [], "refusal (injected)")
        return None


def _jpeg_b64(img: bytes) -> str:
    return base64.b64encode(img).decode("ascii")


# --------------------------------------------------------------------------------------------------------------
class OpenAIDecisionsEngine(BaseEngine):
    name = "openai"
    cloud = True

    def __init__(self, api_key: str | None, model: str = "gpt-6-luna", base_url: str = "https://api.openai.com/v1",
                 faults: FaultInjector | None = None, allow_images: bool = False) -> None:
        super().__init__(faults)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.allow_images = allow_images
        self._client: httpx.AsyncClient | None = None

    def available(self) -> tuple[bool, str]:
        return (True, "key present") if self.api_key else (False, "OPENAI_API_KEY not set")

    async def warmup(self) -> None:
        """Open the TLS connection ahead of the first decision: a cold first call measured ~1.3 s against a deadline
        of 800 ms, warm calls 170-280 ms."""
        if not self.api_key:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        try:
            await self._client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError:
            pass

    @staticmethod
    def build_body(job: DecisionJob, model: str, image_b64: str | None) -> dict[str, Any]:
        user_parts: list[dict[str, Any]] = [{"type": "input_json", "data": job.facts}]
        if image_b64:
            user_parts.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}"})
        questions = []
        for q in job.questions:
            d: dict[str, Any] = {"type": q.kind, "name": q.name, "instructions": q.instructions}
            if q.kind == "choice":
                d["choices"] = [{"value": o.value, "description": o.text()} for o in q.options]
            elif q.kind == "score":
                d["levels"] = [{"label": o.value, "description": o.text()} for o in q.options]
            questions.append(d)
        return {
            "model": model,
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": PREAMBLE}]},
                {"role": "user", "content": user_parts},
            ],
            "questions": questions,
        }

    @staticmethod
    def parse(data: dict[str, Any], questions: list[Question]) -> tuple[list[DecisionAnswer], bool]:
        answers: list[DecisionAnswer] = []
        refused = False
        raw = data.get("answers") or []
        by_name = {q.name: q for q in questions}
        for i, a in enumerate(raw):
            name = a.get("name") or (questions[i].name if i < len(questions) else f"q{i}")
            t = a.get("type")
            if t == "refusal":
                refused = True
                answers.append(DecisionAnswer(name=name, type="refusal"))
            elif t == "choice":
                probs = {p["value"]: float(p["probability"]) for p in a.get("probabilities", []) if "value" in p}
                answers.append(DecisionAnswer(name=name, type="choice", choice=a.get("choice"),
                                              confidence=a.get("confidence"), probabilities=probs))
            elif t == "predicate":
                answers.append(DecisionAnswer(name=name, type="predicate", probability=a.get("probability")))
            elif t == "score":
                q = by_name.get(name)
                probs = {}
                for p in a.get("probabilities", []):
                    label = p.get("label")
                    if label is None and q is not None and isinstance(p.get("value"), int) and p["value"] < len(q.options):
                        label = q.options[p["value"]].value
                    probs[str(label if label is not None else p.get("value"))] = float(p.get("probability", 0.0))
                answers.append(DecisionAnswer(name=name, type="score", score=a.get("score"),
                                              confidence=a.get("confidence"), probabilities=probs))
        return answers, refused

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:
        t0 = time.perf_counter()
        inj = await self._injected(deadline_ms)
        if inj is not None:
            return inj
        if not self.api_key:
            raise EngineError("error", "no API key")
        img = _jpeg_b64(job.image_jpeg) if (job.image_jpeg and self.allow_images) else None
        body = self.build_body(job, self.model or "gpt-6-luna", img)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        try:
            r = await self._client.post(f"{self.base_url}/decisions", json=body,
                                        headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.TimeoutException as e:
            raise EngineError("timeout", f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise EngineError("error", f"{type(e).__name__}: {e}") from e
        lat = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            raise EngineError("error", f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        answers, refused = self.parse(data, job.questions)
        return EngineResult(self.name, data.get("model", self.model), "refusal" if refused and not any(
            a.type != "refusal" for a in answers) else "success", lat, answers, usage=data.get("usage"))


# --------------------------------------------------------------------------------------------------------------
def systemone_questions(questions: list[Question]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for q in questions:
        if q.kind == "predicate":
            out[q.name] = {"type": "noul", "instructions": q.instructions}
        elif q.kind == "choice":
            out[q.name] = {"type": "choice", "instructions": q.instructions,
                           "criteria": {o.value: o.text() for o in q.options}}
        elif q.kind == "score":
            out[q.name] = {"type": "score", "instructions": q.instructions,
                           "criteria": [f"{o.value}: {o.text()}" for o in q.options]}
    return out


def parse_systemone(data: dict[str, Any], questions: list[Question]) -> list[DecisionAnswer]:
    answers: list[DecisionAnswer] = []
    raw = data.get("answers") or {}
    for q in questions:
        a = raw.get(q.name)
        if a is None:
            continue
        t = a.get("type")
        if t == "noul":
            answers.append(DecisionAnswer(name=q.name, type="predicate", probability=a.get("noul")))
        elif t == "choice":
            answers.append(DecisionAnswer(name=q.name, type="choice", choice=a.get("choice"), confidence=a.get("confidence"),
                                          probabilities={k: float(v) for k, v in (a.get("probabilities") or {}).items()}))
        elif t == "score":
            probs = {}
            for k, v in (a.get("probabilities") or {}).items():
                try:
                    probs[q.options[int(k)].value] = float(v)
                except (ValueError, IndexError):
                    probs[str(k)] = float(v)
            answers.append(DecisionAnswer(name=q.name, type="score", score=a.get("score"), confidence=a.get("confidence"),
                                          probabilities=probs))
    return answers


class TypeSafeEngine(BaseEngine):
    name = "typesafe"
    cloud = True

    def __init__(self, api_key: str | None, model: str = "jev-latest", url: str = "https://api.typesafe.ai/v1/systemone",
                 faults: FaultInjector | None = None, allow_images: bool = False) -> None:
        super().__init__(faults)
        self.api_key = api_key
        self.model = model
        self.url = url
        self.allow_images = allow_images
        self._client: httpx.AsyncClient | None = None

    def available(self) -> tuple[bool, str]:
        return (True, "key present") if self.api_key else (False, "TYPESAFE_API_KEY not set")

    @staticmethod
    def build_body(job: DecisionJob, model: str, image_b64: str | None) -> tuple[dict[str, Any], dict[str, str]]:
        state: dict[str, Any] = {"context": PREAMBLE, "facts": job.facts}
        body: dict[str, Any] = {"model": model, "state": state, "questions": systemone_questions(job.questions)}
        headers: dict[str, str] = {}
        if image_b64:
            ref = str(uuid.uuid4())
            state["candidate_tiles"] = ref
            body["assets"] = {ref: {"type": "image", "format": "jpg", "data_base64": image_b64}}
            headers["TypeSafe-Danger-Zone-Images"] = "true"
        return body, headers

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:
        t0 = time.perf_counter()
        inj = await self._injected(deadline_ms)
        if inj is not None:
            return inj
        if not self.api_key:
            raise EngineError("error", "no API key")
        img = _jpeg_b64(job.image_jpeg) if (job.image_jpeg and self.allow_images) else None
        body, extra = self.build_body(job, self.model or "jev-latest", img)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        try:
            r = await self._client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}", **extra})
        except httpx.TimeoutException as e:
            raise EngineError("timeout", f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise EngineError("error", f"{type(e).__name__}: {e}") from e
        lat = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            raise EngineError("error", f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return EngineResult(self.name, data.get("model", self.model), "success", lat,
                            parse_systemone(data, job.questions), usage=data.get("usage"))


class StrandsEngine(BaseEngine):
    name = "strands"
    cloud = False

    def __init__(self, url: str = "http://127.0.0.1:8099/v1/systemone", faults: FaultInjector | None = None) -> None:
        super().__init__(faults)
        self.url = url
        self.model = "strands-decider (local)"
        self._client: httpx.AsyncClient | None = None
        self._probe_ok: bool | None = None
        self._probe_ms = 0.0

    def available(self) -> tuple[bool, str]:
        if self._probe_ok is None:
            return False, "not probed yet"
        return (True, "local server up") if self._probe_ok else (False, f"no server at {self.url}")

    async def probe(self) -> None:
        if time.monotonic() - self._probe_ms < 15.0 and self._probe_ok is not None:
            return
        self._probe_ms = time.monotonic()
        health = self.url.rsplit("/v1/", 1)[0] + "/health"
        try:
            async with httpx.AsyncClient(timeout=0.6) as c:
                r = await c.get(health)
                self._probe_ok = r.status_code == 200
                if self._probe_ok:
                    self.model = r.json().get("model", self.model)
        except Exception:  # noqa: BLE001
            self._probe_ok = False

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:
        t0 = time.perf_counter()
        body = {"state": {"context": PREAMBLE, "facts": job.facts}, "questions": systemone_questions(job.questions)}
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=0.5))
        try:
            r = await self._client.post(self.url, json=body)
        except httpx.HTTPError as e:
            self._probe_ok = False
            raise EngineError("error", f"{type(e).__name__}: {e}") from e
        lat = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            raise EngineError("error", f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return EngineResult(self.name, data.get("model", self.model), "success", lat, parse_systemone(data, job.questions),
                            usage=data.get("usage"))


# --------------------------------------------------------------------------------------------------------------
def rule_answers(job: DecisionJob) -> list[DecisionAnswer]:
    res = RULES[job.task](job.facts)
    out = []
    for q in job.questions:
        v = res.get(q.name)
        if v is None:
            continue
        if q.kind == "choice":
            out.append(DecisionAnswer(name=q.name, type="choice", choice=str(v), confidence=None, probabilities={str(v): 1.0}))
        elif q.kind == "predicate":
            out.append(DecisionAnswer(name=q.name, type="predicate", probability=float(v)))
        elif q.kind == "score":
            idx = int(v)
            out.append(DecisionAnswer(name=q.name, type="score", score=float(idx), confidence=None,
                                      probabilities={q.options[idx].value: 1.0}))
    return out


class RulesEngine(BaseEngine):
    name = "rules"
    model = "deterministic-rules-v1"

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:
        t0 = time.perf_counter()
        ans = rule_answers(job)
        return EngineResult(self.name, self.model, "success", (time.perf_counter() - t0) * 1000.0, ans)

    def decide_sync(self, job: DecisionJob) -> EngineResult:
        return EngineResult(self.name, self.model, "success", 0.05, rule_answers(job))


class MockEngine(BaseEngine):
    """Clearly-labelled stand-in for a cloud decision model (no key). Answers = rules + soft probabilities,
    latency ~ lognormal around 270 ms. Every record it produces carries ``mock=True``."""

    name = "mock"
    model = "MOCK (no API key)"
    mock = True
    cloud = True

    def __init__(self, faults: FaultInjector | None = None, seed: int = 13, median_ms: float = 270.0) -> None:
        super().__init__(faults)
        self.rng = np.random.default_rng(seed)
        self.median_ms = median_ms

    def sample(self, job: DecisionJob) -> EngineResult:
        lat = float(np.clip(self.rng.lognormal(math.log(self.median_ms), 0.35), 110, 1500))
        base = rule_answers(job)
        out: list[DecisionAnswer] = []
        for q in job.questions:
            a = next((x for x in base if x.name == q.name), None)
            if a is None:
                continue
            if q.kind == "choice":
                k = len(q.options)
                top = float(self.rng.uniform(0.72, 0.97)) if k > 1 else 1.0  # one option left: it is certain
                rest = self.rng.dirichlet(np.ones(max(1, k - 1))) * (1 - top)
                probs = {}
                it = iter(rest)
                for o in q.options:
                    probs[o.value] = round(top if o.value == a.choice else float(next(it, 0.0)), 4)
                conf = (top - 1 / k) / (1 - 1 / k) if k > 1 else 1.0
                out.append(DecisionAnswer(name=q.name, type="choice", choice=a.choice, confidence=round(conf, 3),
                                          probabilities=probs))
            elif q.kind == "predicate":
                p = float(np.clip((a.probability or 0.5) + self.rng.normal(0, 0.08), 0.02, 0.98))
                out.append(DecisionAnswer(name=q.name, type="predicate", probability=round(p, 3)))
            else:
                out.append(a)
        return EngineResult(self.name, self.model, "success", lat, out, mock=True)

    async def decide(self, job: DecisionJob, deadline_ms: float) -> EngineResult:
        inj = await self._injected(deadline_ms)
        if inj is not None:
            inj.mock = True
            return inj
        res = self.sample(job)
        await asyncio.sleep(res.latency_ms / 1000.0)
        return res
