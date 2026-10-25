import asyncio
import time

import pytest

from overwatch.config import load_config
from overwatch.decisions.engines import BaseEngine, OpenAIDecisionsEngine, TypeSafeEngine, parse_systemone
from overwatch.decisions.router import DecisionRouter
from overwatch.decisions.tasks import d1_reacquire, d5_priority
from overwatch.decisions.types import EngineResult
from overwatch.faults import FaultInjector

JOB = d1_reacquire({"time_since_loss_s": 1.0, "occluder_near_last_seen": False})


def test_openai_body_and_parse_match_documented_shapes():
    body = OpenAIDecisionsEngine.build_body(JOB, "gpt-6-luna", None)
    assert body["model"] == "gpt-6-luna"
    assert body["input"][1]["content"][0] == {"type": "input_json", "data": JOB.facts}
    q = {x["name"]: x for x in body["questions"]}
    assert q["search_action"]["type"] == "choice" and {"value", "description"} <= set(q["search_action"]["choices"][0])
    assert q["urgency"]["type"] == "score" and q["urgency"]["levels"][0]["label"] == "low"
    resp = {"answers": [
        {"type": "choice", "name": "search_action", "choice": "orbit_last_seen", "confidence": 0.81,
         "probabilities": [{"value": "orbit_last_seen", "probability": 0.86}, {"value": "hold_and_alert", "probability": 0.14}]},
        {"type": "predicate", "name": "target_likely_occluded", "probability": 0.92},
        {"type": "score", "name": "urgency", "score": 1.1, "confidence": 0.55,
         "probabilities": [{"value": 0, "label": "low", "probability": 0.1}, {"value": 1, "label": "moderate", "probability": 0.7},
                           {"value": 2, "label": "high", "probability": 0.2}]},
    ]}
    answers, refused = OpenAIDecisionsEngine.parse(resp, JOB.questions)
    a = {x.name: x for x in answers}
    assert not refused and a["search_action"].choice == "orbit_last_seen" and a["search_action"].probabilities["hold_and_alert"] == 0.14
    assert a["target_likely_occluded"].probability == 0.92 and a["urgency"].probabilities["moderate"] == 0.7
    ref, refused = OpenAIDecisionsEngine.parse({"answers": [{"type": "refusal"}]}, JOB.questions)
    assert refused and ref[0].type == "refusal" and ref[0].name == "search_action"


def test_typesafe_body_and_parse():
    body, headers = TypeSafeEngine.build_body(JOB, "jev-latest", "AAAA")
    qs = body["questions"]
    assert qs["target_likely_occluded"]["type"] == "noul"
    assert qs["search_action"]["criteria"]["hold_and_alert"].startswith("Hover")
    assert isinstance(qs["urgency"]["criteria"], list)
    ref = body["state"]["candidate_tiles"]
    assert body["assets"][ref]["data_base64"] == "AAAA" and headers["TypeSafe-Danger-Zone-Images"] == "true"
    resp = {"model": "jev-1.13.0", "answers": {
        "search_action": {"type": "choice", "choice": "climb_and_widen", "confidence": 0.7,
                          "probabilities": {"climb_and_widen": 0.8, "other": 0.2}},
        "target_likely_occluded": {"type": "noul", "noul": 0.3},
        "urgency": {"type": "score", "score": 1.7, "confidence": 0.5, "legend": {"0": "x"},
                    "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8}}}}
    a = {x.name: x for x in parse_systemone(resp, JOB.questions)}
    assert a["search_action"].choice == "climb_and_widen" and a["target_likely_occluded"].probability == 0.3
    assert a["urgency"].probabilities["high"] == 0.8


class SlowEngine(BaseEngine):
    name = "slow"
    model = "slow"
    cloud = True

    async def decide(self, job, deadline_ms):
        await asyncio.sleep(2.0)
        return EngineResult("slow", "slow", "success", 2000.0)


def test_router_timeout_falls_back_to_rules_within_deadline():
    cfg = load_config()
    cfg.decisions.deadline_ms = 600
    cfg.decisions.shadow = []
    r = DecisionRouter(cfg, FaultInjector(), "LIVE")
    r.engines["openai"] = SlowEngine()
    r.engines["openai"].available = lambda: (True, "stub")  # type: ignore[method-assign]
    t0 = time.perf_counter()
    recs = asyncio.run(r.run(JOB, lambda: 0.0))
    dt = (time.perf_counter() - t0) * 1000
    assert dt < 650
    assert recs[0].engine == "rules" and recs[0].status == "fallback" and recs[0].acted
    assert "timeout" in recs[0].reason and r.ai_paused


def test_injected_refusal_means_no_optional_action():
    cfg = load_config()
    cfg.decisions.shadow = []
    f = FaultInjector()
    f.set_manual("api_refusal", True)
    r = DecisionRouter(cfg, f, "SIMULATED")
    job = d5_priority({"pending": [{"event_id": "e-1", "kind": "ZONE_ENTRY", "text": "x"}]})
    recs = asyncio.run(r.run(job, lambda: 0.0))
    assert recs[0].status == "refusal" and not recs[0].acted


def test_no_key_uses_labelled_mock_in_sim_and_rules_live():
    cfg = load_config()
    cfg.secrets.openai_api_key = None
    assert DecisionRouter(cfg, FaultInjector(), "SIMULATED").resolve_primary() == "mock"
    assert DecisionRouter(cfg, FaultInjector(), "LIVE").resolve_primary() == "rules"
