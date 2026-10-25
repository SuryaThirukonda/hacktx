import numpy as np
import pytest

from overwatch.config import LocksCfg
from overwatch.tracking.lock import Lock, TrackInfo
from overwatch.tracking.memory import SubjectMemory


def info(tid="A", xy=(10., 10.), hist=None):
    return TrackInfo(tid, "CONFIRMED", True, (100, 100, 160, 300), .95, xy, .4,
                     np.array([1., 0.]) if hist is None else hist, 10, velocity=(1., 0.))


def subject():
    lock = Lock("subject", LocksCfg())
    target = info()
    lock.engage(target, 1000)
    memory = SubjectMemory("INC", "SESSION", "enu:INC", lock, "object-1")
    memory.update(lock, target, 1000, 1, 1000, True, (), target.hist)
    return lock, memory


def test_continuous_measurements_and_new_track_keep_logical_subject():
    lock, memory = subject()
    original = memory.logical_id
    lock.confirm_candidate(info("B", (11, 10)), 1200, "operator")
    view = memory.update(lock, info("B", (11, 10)), 1200, 2, 1200, True, (), np.array([1., 0.]))
    assert view["logical_subject_id"] == original
    assert view["associated_track_ids"] == ["A", "B"]
    assert view["state"] == "VISIBLE" and view["position_kind"] == "measured"
    assert len(view["motion_history"]) == 2
    assert view["appearance_signature"] == [1, 0]


@pytest.mark.parametrize("state,time,expected", [("COAST", 1800, "TEMPORARILY_LOST"),
                                                ("REACQUIRE", 3500, "REACQUIRING"),
                                                ("UNCERTAIN", 3500, "UNCERTAIN"),
                                                ("HOLD", 22000, "LOST")])
def test_missing_measurements_are_explicit_predictions(state, time, expected):
    lock, memory = subject()
    lock.state = state
    view = memory.update(lock, None, time, 1, 1000, True, ("missing measurement",), None)
    assert view["state"] == expected
    assert view["measured_position"] is None
    assert view["last_verified_ts_ms"] == 1000
    assert view["predicted_position"] and view["position_kind"] == "predicted"
    assert view["uncertainty_m"] > .4
    assert len(view["motion_history"]) == 1


def test_staleness_and_reconnection_do_not_fabricate_a_fresh_point():
    lock, memory = subject()
    stale = memory.update(lock, info(), 1900, 1, 1000, False, (), None)
    assert stale["state"] == "TEMPORARILY_LOST" and stale["measured_position"] is None
    fresh = memory.update(lock, info(xy=(12, 10)), 2000, 2, 2000, True, (), None)
    assert fresh["logical_subject_id"] == stale["logical_subject_id"]
    assert fresh["state"] == "VISIBLE" and fresh["last_verified_ts_ms"] == 2000


def test_crossing_similar_people_stays_uncertain():
    lock, memory = subject()
    lock.last_neighbor_ms = 1000
    lock.update({"B": info("B", (11, 10)), "C": info("C", (10.8, 10))}, set(), 2200)
    view = memory.update(lock, None, 2200, 2, 2200, True, (), None)
    assert lock.track_id == "A" and view["logical_subject_id"] == memory.logical_id
    assert view["state"] == "UNCERTAIN" and len(view["candidate_evidence"]) == 2


def test_wrong_appearance_and_direction_do_not_rebind():
    lock, _ = subject()
    wrong = info("wrong", (11, 10), np.array([0., 1.]))
    wrong.velocity = (-2, 0)
    for stamp in (1800, 2500, 4000):
        lock.update({"wrong": wrong}, set(), stamp)
    assert lock.track_id == "A"


def test_quality_problem_does_not_extend_verified_history():
    lock, memory = subject()
    view = memory.update(lock, info(xy=(15, 10)), 1300, 2, 1300, True, ("overlapping people",), None)
    assert view["state"] == "UNCERTAIN"
    assert view["last_verified_position"]["x"] == 10
    assert view["measured_position"] is None
