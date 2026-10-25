"""Object memory: persistent ids through occlusion and tracker id switches, explained absences, no guessing."""

import numpy as np

from overwatch.tracking.memory import LOST, OCCLUDED, OUT_OF_VIEW, VISIBLE, ObjectMemory, TrackView

RED = np.array([1.0, 0, 0, 0, 0, 0, 0, 0], np.float32)
RED2 = np.array([0.95, 0.05, 0, 0, 0, 0, 0, 0], np.float32)
BLUE = np.array([0, 0, 0, 0, 0, 0, 0, 1.0], np.float32)


def tv(tid, xy, hist=RED, matched=True, err=0.5):
    return TrackView(tid, "person", matched, True, xy, err, (100, 100, 110, 130), hist)


def run(mem, frames, in_view=lambda xy: True, covered=lambda o: False, t0=0.0, dt=100.0):
    t = t0
    for f in frames:
        mem.update(t, f, in_view, covered)
        t += dt
    return t


def test_occlusion_then_new_track_continues_the_same_object():
    m = ObjectMemory()
    t = run(m, [[tv("A", (x * 0.1, 0.0))] for x in range(10)])
    oid = m.by_track["A"]
    # under canopy for 3 s: the memory explains the absence
    t = run(m, [[] for _ in range(30)], covered=lambda o: True, t0=t)
    assert m.objects[oid].state == OCCLUDED and m.objects[oid].confidence(t) < 1
    # a new tracker id comes out the other side, close by and looking the same
    run(m, [[tv("B", (1.3, 0.2), RED2)]], t0=t)
    assert m.by_track["B"] == oid and m.objects[oid].link == "continued" and m.objects[oid].state == VISIBLE
    assert any(e["kind"] == "object_reattached" for e in m.events)


def test_states_out_of_view_and_lost_with_hysteresis():
    m = ObjectMemory()
    t = run(m, [[tv("A", (0.0, 0.0))]])
    oid = m.by_track["A"]
    t = run(m, [[]], in_view=lambda xy: False, t0=t)
    assert m.objects[oid].state == VISIBLE  # one frame is not enough to change state
    t = run(m, [[], []], in_view=lambda xy: False, t0=t)
    assert m.objects[oid].state == OUT_OF_VIEW
    m2 = ObjectMemory()
    t = run(m2, [[tv("A", (0.0, 0.0))]])
    run(m2, [[] for _ in range(12)], t0=t)  # in view, uncovered, not detected
    assert next(iter(m2.objects.values())).state == LOST


def test_never_attaches_when_two_people_fit_or_appearance_differs():
    m = ObjectMemory()
    t = run(m, [[tv("A", (0.0, 0.0)), tv("B", (3.0, 0.0))]])
    t = run(m, [[] for _ in range(10)], covered=lambda o: True, t0=t)
    run(m, [[tv("C", (1.5, 0.0))]], t0=t)  # halfway between two hidden people: ambiguous
    assert m.by_track["C"] not in (m.by_track["A"], m.by_track["B"])
    m2 = ObjectMemory()
    t = run(m2, [[tv("A", (0.0, 0.0), RED)]])
    t = run(m2, [[] for _ in range(10)], covered=lambda o: True, t0=t)
    run(m2, [[tv("D", (0.3, 0.0), BLUE)]], t0=t)  # right place, different clothes: a different person
    assert m2.by_track["D"] != m2.by_track["A"]


def test_far_reattachment_is_only_possible_and_old_objects_are_forgotten():
    m = ObjectMemory()
    t = run(m, [[tv("A", (0.0, 0.0))]])
    t = run(m, [[] for _ in range(60)], t0=t)  # lost for 6 s
    run(m, [[tv("E", (7.0, 0.0), RED2)]], t0=t)  # within walking reach, but the absence was never explained
    oid = m.by_track["E"]
    assert oid == m.by_track["A"] and m.objects[oid].link == "possible"
    m2 = ObjectMemory()
    t = run(m2, [[tv("A", (0.0, 0.0))]])
    run(m2, [[] for _ in range(400)], t0=t)  # 40 s lost: confidence fades below the floor
    assert not m2.objects and m2.forgotten == 1


def test_no_positions_no_memory():
    m = ObjectMemory()
    run(m, [[TrackView("A", "person", True, True, None, None, (0, 0, 1, 1), RED)]], in_view=None, covered=None)
    assert not m.objects
