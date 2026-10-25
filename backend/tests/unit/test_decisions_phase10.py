"""Phase 10: D6-D11 designed cases, unsafe-option removal, answer validation, freshness, session counter, cost guard
and the rules-only label."""

import asyncio

import pytest

from overwatch.config import load_config
from overwatch.decisions.engines import rule_answers
from overwatch.decisions.recommendations import DECISION_TO_RECOMMENDATION, SPECS
from overwatch.decisions.router import DecisionRouter, probabilities_problem
from overwatch.decisions.tasks import BUILDERS, PRIMARY_QUESTION, prepare
from overwatch.decisions.types import EngineResult
from overwatch.faults import FaultInjector
from overwatch.schemas import DecisionAnswer

# Hand-written designed cases: (task, facts, expected rules answer, options that must have been removed)
DESIGNED = [
    ("D6_obstacle", {"obstacle_range_m": 2.5, "closing_mps": 1.0, "clearance_left_m": 5, "clearance_right_m": 5,
                     "altitude_m": 20, "ceiling_m": 60}, "hold_position", set()),
    ("D6_obstacle", {"obstacle_range_m": 8, "closing_mps": 2.0, "clearance_left_m": 1.5, "clearance_right_m": 6,
                     "altitude_m": 20, "ceiling_m": 60}, "sidestep_right", {"sidestep_left"}),
    ("D6_obstacle", {"obstacle_range_m": 8, "closing_mps": 2.0, "clearance_left_m": 1, "clearance_right_m": 2,
                     "altitude_m": 58, "ceiling_m": 60}, "hold_position", {"sidestep_left", "sidestep_right", "climb"}),
    ("D7_lead_triage", {"leads": [{"id": "L1", "age_s": 600, "distance_m": 50, "uncertainty_m": 3},
                                  {"id": "L2", "age_s": 30, "distance_m": 120, "uncertainty_m": 4},
                                  {"id": "L3", "age_s": 10, "distance_m": 20, "uncertainty_m": None, "reviewed": True}]},
     "L2", set()),
    ("D7_lead_triage", {"leads": []}, "none", set()),
    ("D8_next_sector", {"sectors": [{"id": "S1", "poa": 0.4, "coverage_pct": 90, "distance_m": 50},
                                    {"id": "S2", "poa": 0.3, "coverage_pct": 10, "distance_m": 300},
                                    {"id": "S3", "poa": 0.3, "coverage_pct": 99, "distance_m": 10}]}, "S2", set()),
    ("D9_route", {"routes": [{"id": "R1", "length_m": 300, "eta_s": 240, "max_water_depth_m": 0.8},
                             {"id": "R2", "length_m": 450, "eta_s": 360, "max_water_depth_m": 0.1},
                             {"id": "R3", "length_m": 200, "eta_s": 150, "hazards_crossed": 1}]}, "R2", {"R1", "R3"}),
    ("D9_route", {"routes": [{"id": "R1", "length_m": 300, "eta_s": 240, "max_water_depth_m": 0.8}]}, "hold", {"R1"}),
    ("D10_dispatch", {"requests": [{"id": "Q1", "priority": 2}, {"id": "Q2", "priority": 1}],
                      "teams": [{"id": "T1", "available": True, "eta_s": {"Q1": 100, "Q2": 400}},
                                {"id": "T2", "available": True, "eta_s": {"Q1": 300, "Q2": 200}},
                                {"id": "T3", "available": False, "eta_s": {"Q1": 10, "Q2": 10}}]}, "T2>Q2", set()),
    ("D11_opening", {"blocked_marked": True, "in_view": True, "obstructed_fraction": 0.0}, "obstruction_marked", set()),
    ("D11_opening", {"in_view": True, "obstructed_fraction": 0.6}, "obstruction_seen", set()),
    ("D11_opening", {"in_view": True, "smoke_layer": True, "obstructed_fraction": 0.0}, "unclear",
     {"obstruction_seen", "no_obstruction_seen"}),  # nothing can be "seen" through smoke
    ("D11_opening", {"in_view": False, "obstructed_fraction": 0.0}, "unclear", {"obstruction_seen", "no_obstruction_seen"}),
]


@pytest.mark.parametrize("task,facts,expected,removed", DESIGNED)
def test_designed_cases_rules_baseline(task, facts, expected, removed):
    job = prepare(BUILDERS[task](facts))
    q = job.questions[0]
    values = {o.value for o in q.options}
    assert not (removed & values), "unsafe options must be removed before any engine is asked"
    assert {r["value"] for r in job.context.get("removed_options", [])} == removed
    ans = next(a for a in rule_answers(job) if a.name == PRIMARY_QUESTION[task])
    assert ans.choice == expected
    assert ans.choice in values  # the rules never pick a removed option
    assert any(o.value in ("other", "none", "hold", "unclear", "hold_position") for o in q.options)  # escape option


def test_options_carry_measured_outcomes():
    job = BUILDERS["D9_route"]({"routes": [{"id": "R2", "length_m": 450, "eta_s": 360}]})
    o = job.questions[0].options[0]
    assert o.outcome == "450 m, about 6.0 min on foot" and "Measured: 450 m" in o.text()


def test_every_sim_task_maps_to_an_allowed_recommendation():
    assert set(DECISION_TO_RECOMMENDATION) == set(BUILDERS)
    assert set(DECISION_TO_RECOMMENDATION.values()) <= set(SPECS)


def test_probability_validation():
    opts = {"a", "b", "c"}
    assert probabilities_problem({"a": 0.7, "b": 0.2, "c": 0.1}, opts, "a", 0.05) is None
    assert "outside the options" in probabilities_problem({"a": 0.7, "z": 0.3}, opts, "a", 0.05)
    assert "sum to 0.60" in probabilities_problem({"a": 0.4, "b": 0.2}, opts, "a", 0.05)
    assert "not the most probable" in probabilities_problem({"a": 0.2, "b": 0.8}, opts, "a", 0.05)
    assert "outside [0, 1]" in probabilities_problem({"a": 1.4, "b": -0.4}, opts, "a", 0.05)


def _router(**dc):
    cfg = load_config()
    cfg.decisions.shadow = []
    for k, v in dc.items():
        setattr(cfg.decisions, k, v)
    return DecisionRouter(cfg, FaultInjector(), "LIVE")


class StubEngine:
    mock = False
    model = "stub"
    cloud = True

    def __init__(self, answers, usage=None):
        self.answers = answers
        self.usage = usage

    def available(self):
        return True, "stub"

    async def decide(self, job, deadline_ms):
        return EngineResult("openai", "stub", "success", 120.0, list(self.answers), usage=self.usage)


def _d9_job():
    return BUILDERS["D9_route"]({"routes": [{"id": "R1", "length_m": 300, "eta_s": 240},
                                           {"id": "R2", "length_m": 450, "eta_s": 360}]})


def test_model_answer_with_bad_probabilities_is_not_acted_on():
    r = _router()
    r.engines["openai"] = StubEngine([DecisionAnswer(name="route", type="choice", choice="R2", confidence=0.9,
                                                     probabilities={"R2": 0.9, "R9": 0.1})])
    rec = asyncio.run(r.run(_d9_job(), lambda: 0.0))[0]
    assert not rec.acted and "invalid probabilities" in rec.reason and r.rejected_invalid == 1
    assert rec.facts["_rules_baseline"] == "R1"  # the rules answer is still recorded for the comparison


def test_answer_outside_options_and_removed_option_rejected():
    r = _router()
    job = BUILDERS["D9_route"]({"routes": [{"id": "R1", "length_m": 300, "eta_s": 240, "max_water_depth_m": 1.0},
                                           {"id": "R2", "length_m": 450, "eta_s": 360}]})
    r.engines["openai"] = StubEngine([DecisionAnswer(name="route", type="choice", choice="R1", confidence=0.95,
                                                     probabilities={"R1": 0.95, "R2": 0.05})])
    rec = asyncio.run(r.run(job, lambda: 0.0))[0]
    assert not rec.acted and "invalid choice 'R1'" in rec.reason  # R1 was removed as unsafe before asking


def test_stale_and_old_session_answers_are_dropped():
    r = _router()
    r.engines["openai"] = StubEngine([DecisionAnswer(name="route", type="choice", choice="R1", confidence=0.9,
                                                     probabilities={"R1": 0.9, "R2": 0.05, "hold": 0.05})])
    job = _d9_job()
    job.created_ms = 0.0
    job.context["session"] = r.session
    recs = asyncio.run(r.run(job, lambda: 0.0))
    assert recs[0].acted
    assert r.drop_if_stale(job, recs, 1400.0) is None and recs[0].acted
    assert "dropped" in r.drop_if_stale(job, recs, 1600.0)
    assert not recs[0].acted and recs[0].facts["_dropped"]
    job2 = _d9_job()
    job2.created_ms = 0.0
    job2.context["session"] = r.session
    recs2 = asyncio.run(r.run(job2, lambda: 0.0))
    r.new_session()
    assert r.drop_if_stale(job2, recs2, 10.0) == "answer from an earlier session dropped"
    assert r.dropped_stale == 2


def test_cost_guard_switches_to_rules_with_a_warning():
    r = _router(max_model_calls_per_session=2)
    r.engines["openai"] = StubEngine([DecisionAnswer(name="route", type="choice", choice="R1", confidence=0.9,
                                                     probabilities={"R1": 0.9, "R2": 0.05, "hold": 0.05})],
                                     usage={"total_tokens": 900})
    for _ in range(2):
        assert asyncio.run(r.run(_d9_job(), lambda: 0.0))[0].engine == "openai"
    assert r.session_tokens == 1800 and r.budget_exhausted()
    rec = asyncio.run(r.run(_d9_job(), lambda: 0.0))[0]
    assert rec.engine == "rules"
    assert r.status().mode_label.startswith("rules only (session budget used: 2 calls")
    r.new_session()  # a new mission run gets a fresh budget
    assert r.resolve_primary() == "openai"


def test_rules_only_label_without_a_key():
    cfg = load_config()
    cfg.secrets.openai_api_key = None
    cfg.decisions.allow_mock = False
    r = DecisionRouter(cfg, FaultInjector(), "SIMULATED")
    assert r.resolve_primary() == "rules"
    assert r.status().mode_label == "rules only (no API key)"
    rec = asyncio.run(r.run(_d9_job(), lambda: 0.0))[0]
    assert rec.engine == "rules" and rec.acted and rec.selected == "R1"


def test_one_option_left_needs_no_model_call():
    r = _router()
    called = []

    class Spy(StubEngine):
        async def decide(self, job, deadline_ms):
            called.append(job.task)
            return await super().decide(job, deadline_ms)

    r.engines["openai"] = Spy([])
    job = BUILDERS["D9_route"]({"routes": [{"id": "R1", "length_m": 300, "eta_s": 240, "max_water_depth_m": 1.0}]})
    rec = asyncio.run(r.run(job, lambda: 0.0))[0]
    assert called == [] and rec.engine == "rules" and rec.selected == "hold" and "no model call" in rec.reason
