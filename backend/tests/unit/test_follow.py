"""Phase 13: Follow mode. Designation by a person only, lost marking, no silent identity switch, motion statistics
only from verified positions of the confirmed target."""

from types import SimpleNamespace

from overwatch.evaluation.gt_eval import _iou
from overwatch.evaluation.scenario_runner import HeadlessRunner
from overwatch.missions.follow import FollowTracker


def lock(state, tid, verified=True):
    return SimpleNamespace(state=state, track_id=tid, verified=verified)


def trk(x, y, visible=True, err=0.5):
    return {"box": (0, 0, 10, 10), "xy": (x, y), "err": err, "visible": visible}


def test_designate_trail_lost_rebind_and_redesignation():
    events = []
    f = FollowTracker(lambda k, p: events.append((k, p)))
    f.update(lock("LOCKED", "A"), {"A": trk(0, 0)}, 0)
    assert f.s.trail == [] and f.view(0)["status"] == "none"  # nothing before a person designates
    f.designate("A", "op1", 0)
    for i in range(1, 31):  # 3 s walking east at 1.5 m/s
        f.update(lock("LOCKED", "A"), {"A": trk(0.15 * i, 0)}, i * 100)
    m = f.motion(3000)
    assert m["valid"] and 1.3 <= m["speed_mps"] <= 1.7 and m["heading_deg"] == 90
    f.update(lock("COAST", "A"), {"A": trk(4.5, 0, visible=False)}, 3200)
    assert f.s.status == "lost" and events[-1][0] == "follow_lost"
    # the lock re-binds to a look-alike on its own: logged, shown unverified, never counted as the target
    f.update(lock("LOCKED", "B", verified=False), {"B": trk(9, 1)}, 4000)
    kinds = [k for k, _ in events]
    assert "follow_rebind_unverified" in kinds and f.s.status == "unverified"
    for i in range(1, 21):
        f.update(lock("LOCKED", "B"), {"B": trk(9 - 0.2 * i, 1)}, 4000 + i * 100)
    assert all(p.identity == "unverified" for p in f.s.trail if p.track_id == "B")
    assert f.motion(6000)["distance_m"] == m["distance_m"]  # B's movement is not the target's
    # a person re-designates: logged with who did it; the old identity is not merged in
    f.designate("B", "op1", 6100)
    assert events[-1][0] == "follow_redesignated" and events[-1][1]["by"] == "op1"
    assert f.s.confirmed_tracks == {"B"} and f.s.status == "tracking"
    f.release("op1", 7000)
    assert events[-1][0] == "follow_released"


def test_confirming_a_rebind_requires_one():
    f = FollowTracker(lambda k, p: None)
    f.designate("A", "op", 0)
    try:
        f.confirm_binding("op", 10)
    except ValueError:
        pass
    else:
        raise AssertionError("confirm without an unverified binding must fail")
    f.update(lock("LOCKED", "C", verified=False), {"C": trk(1, 1)}, 100)
    f.confirm_binding("op", 200)
    assert "C" in f.s.confirmed_tracks and f.s.status == "tracking"


def test_no_motion_statistics_without_verified_positions():
    f = FollowTracker(lambda k, p: None)
    f.designate("A", "op", 0)
    for i in range(30):
        f.update(lock("LOCKED", "A"), {"A": {"box": (0, 0, 1, 1), "xy": None, "err": None, "visible": True}}, i * 100)
    assert f.motion(3000) == {"valid": False, "reason": "no verified positions of the confirmed target yet"}


def test_lookalike_crossing_ow4_never_confirms_the_decoy():
    """OW4: two people in red cross paths. Whatever the lock does, the Follow target is never confirmed on the decoy."""
    events = []
    f = FollowTracker(lambda k, p: events.append(k))
    runner = HeadlessRunner(None, "OW4")
    wrong_confirmed = steps_on_target = 0
    last = [-1e9]
    bound: list[str] = []  # the tracks the lock was bound to, in order

    def on_step(r: HeadlessRunner, t: float) -> None:
        nonlocal wrong_confirmed, steps_on_target
        if t - last[0] < 200:
            return
        last[0] = t
        p = r.pipeline
        lk = p.locks["subject"].snapshot(t)
        if lk.track_id and (not bound or bound[-1] != lk.track_id):
            bound.append(lk.track_id)
        snap = {x.track_id: x for x in p.tracks_snapshot(t)}
        if f.s.designation == 0 and lk.state == "LOCKED" and lk.track_id:
            f.designate(lk.track_id, "operator (scenario script)", t)  # the scenario's scripted operator lock
        tracks = {k: {"box": v.box_xyxy_px, "xy": v.ground_xy_m, "err": v.ground_error_m,
                      "visible": v.state != "LOST" and v.visibility != "lost"} for k, v in snap.items()}
        f.update(lk, tracks, t)
        if not f.s.trail or f.s.trail[-1].t_ms != t or f.s.trail[-1].identity != "confirmed":
            return
        box = snap[f.s.track_id].box_xyxy_px if f.s.track_id in snap else None
        gt = r.sim.last_gt.get("actors", {})
        if box is None:
            return
        iou = {aid: _iou(box, g["box_xyxy"]) for aid, g in gt.items() if g.get("box_xyxy")}
        s_iou, d_iou = iou.get("subject", 0.0), iou.get("decoy", 0.0)
        # count only unambiguous steps: while the two overlap at the crossing, ground truth cannot tell them apart
        if d_iou >= 0.3 and d_iou > s_iou + 0.1:
            wrong_confirmed += 1
        elif s_iou >= 0.3 and s_iou > d_iou + 0.1:
            steps_on_target += 1

    runner.run(on_step=on_step)
    assert f.s.designation == 1 and "follow_designated" in events
    assert steps_on_target > 20, "the target was hardly tracked"
    assert wrong_confirmed == 0, f"Follow confirmed the look-alike in {wrong_confirmed} steps"
    # in OW4 the subject leaves the camera view: if the lock re-binds to another track (the look-alike), Follow must
    # say so; if the lock stays on the lost track (the re-bind gate rejected the look-alike), Follow reports the loss
    assert "follow_lost" in events
    if len(bound) > 1:
        assert "follow_rebind_unverified" in events
    else:
        assert "follow_rebind_unverified" not in events and wrong_confirmed == 0
