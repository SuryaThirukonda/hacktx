from overwatch.config import load_config
from overwatch.evaluation.scenario_runner import HeadlessRunner


def _kinds(res):
    return [(round(e["ts_ms"]), e["kind"]) for e in res.events]


def test_replay_determinism_same_inputs_same_events():
    a = HeadlessRunner(load_config(), "OW1", seed=0).run(9.0)
    b = HeadlessRunner(load_config(), "OW1", seed=0).run(9.0)
    assert _kinds(a) == _kinds(b)
    assert any(k == "CLOSING_FAST" for _, k in _kinds(a))


def test_new_session_resets_track_ids_and_locks():
    r = HeadlessRunner(load_config(), "S01")
    r.run(4.0)
    p = r.pipeline
    assert p.locks["subject"].active
    old_epoch = p.tracker.epoch
    p.reset_session("s-new")
    assert p.tracker.epoch == old_epoch + 1
    assert not p.locks["subject"].active and not p.raw_tracks and not p.world_kf


def test_record_then_replay_is_deterministic(tmp_path):
    """S18: identical recorded inputs replayed twice give identical events (same detector)."""
    from overwatch.clock import SimClock
    from overwatch.decisions.router import DecisionRouter, SimTimeDecisionService
    from overwatch.evaluation.metrics import Metrics
    from overwatch.faults import FaultInjector
    from overwatch.pipeline import Pipeline
    from overwatch.replay.player import replay_headless
    from overwatch.replay.recorder import Recorder

    cfg = load_config()
    runner = HeadlessRunner(cfg, "OW1")
    rec = Recorder(cfg, "s-test", "SIMULATED", "OW1", root=tmp_path)
    runner.pipeline.recorder = rec
    runner.run(9.0)
    rec.close()
    assert rec.counts["drone_frames"] > 100 and rec.counts["telemetry"] > 50

    def replay_once():
        clock = SimClock()
        faults = FaultInjector()
        router = DecisionRouter(cfg, faults, "REPLAY")
        p = Pipeline(cfg, clock, "REPLAY", faults, Metrics(), router, None, "s-replay")
        p.decisions = SimTimeDecisionService(router, p.apply_decision, clock.now_ms, faults)
        n = replay_headless(p, rec.dir, clock)
        return n, [e.kind for e in p.events], p.detector.model_id

    n1, ev1, det1 = replay_once()
    n2, ev2, det2 = replay_once()
    assert n1 == n2 and n1 > 200
    assert ev1 == ev2
    assert det1 == "sim-blob-v1"  # origin provenance (synthetic) honoured on replay
    assert ev1.count("LOCK_ENGAGED") >= 2  # recorded operator locks re-applied at the same moments
    assert "CLOSING_FAST" in ev1  # ... so the replay reproduces the original critical alert


def test_scripted_conflicts_are_adjudicated_against_rules_and_truth():
    """S16: each scripted conflict yields one logged D5 decision carrying the rules baseline."""
    from overwatch.evaluation.gt_eval import adjudicate
    from overwatch.sim.scenarios import get_scenario

    r = HeadlessRunner(load_config(), "S16")
    res = r.run(12.0)
    adj = adjudicate(res, get_scenario("S16"))
    done = [c for c in adj["conflicts"] if c["t_s"] < 11.0]
    assert [c["id"] for c in done] == ["C1", "C2", "C3"]
    assert all(c["decision_id"] and c["rules"] for c in done)
    c2 = next(c for c in done if c["id"] == "C2")
    assert c2["truth"] == "no_alert" and c2["rules"] == "POSITION_CALLOUT"  # fixed priorities ignore the busy responder
    d5 = [d for d in res.decisions if d["task"] == "D5_priority" and d["facts"].get("responder_busy_with_obstacle")]
    assert d5, "C2 must reach D5 while the LiDAR obstacle alert is active"


def test_scripted_events_are_refused_outside_simulation():
    r = HeadlessRunner(load_config(), "S16")
    r.pipeline.mode = "LIVE"
    assert r.pipeline.inject_scripted_events("C1", [{"kind": "REACQUIRED", "text": "x"}], 0.0) == 0
    assert r.pipeline._injected == []


def test_replay_reinjects_recorded_scripted_conflicts(tmp_path):
    """A recorded S16 session replays its scripted conflicts at the same moments (faithful replay)."""
    from overwatch.clock import SimClock
    from overwatch.decisions.router import DecisionRouter, SimTimeDecisionService
    from overwatch.evaluation.metrics import Metrics
    from overwatch.faults import FaultInjector
    from overwatch.pipeline import Pipeline
    from overwatch.replay.player import replay_headless
    from overwatch.replay.recorder import Recorder

    cfg = load_config()
    runner = HeadlessRunner(cfg, "S16")
    rec = Recorder(cfg, "s-test", "SIMULATED", "S16", root=tmp_path)
    runner.pipeline.recorder = rec
    runner.run(6.0)
    rec.close()
    original = sorted((round(e.ts_ms), e.text) for e in runner.pipeline.events if e.source == "scripted")
    assert len(original) == 3  # C1 (two events) + C2

    clock = SimClock()
    faults = FaultInjector()
    router = DecisionRouter(cfg, faults, "REPLAY")
    p = Pipeline(cfg, clock, "REPLAY", faults, Metrics(), router, None, "s-replay")
    p.decisions = SimTimeDecisionService(router, p.apply_decision, clock.now_ms, faults)
    replay_headless(p, rec.dir, clock)
    assert sorted((round(e.ts_ms), e.text) for e in p.events if e.source == "scripted") == original


def test_decision_gating_survives_a_session_restart():
    """Rate limits are kept in session time; after a restart (clock back to 0) decisions must flow again."""
    from overwatch.decisions.router import DecisionRouter, DecisionService
    from overwatch.decisions.tasks import d4_motion
    from overwatch.faults import FaultInjector

    t = {"now": 50_000.0}
    svc = DecisionService(DecisionRouter(load_config(), FaultInjector(), "SIMULATED"), lambda job, recs: None,
                          lambda: t["now"])
    assert svc.gate(d4_motion({"speed_mps": 1.0}))
    t["now"] = 1_000.0  # scenario restarted
    assert svc.gate(d4_motion({"speed_mps": 1.2})), "a restart must not block decisions until the old time is passed"
