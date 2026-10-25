import numpy as np

from overwatch.config import AlertsCfg, LocksCfg, PhoneCfg, TrackerCfg
from overwatch.events.rules import RuleEngine, RuleInput
from overwatch.schemas import CalibrationStatus, PhoneSummary, RelativeKinematics
from overwatch.tracking.lock import Lock, TrackInfo
from overwatch.vision.detectors import Detection
from overwatch.vision.tracker import ByteTracker


def test_tracker_keeps_ids_and_expires():
    cfg = TrackerCfg(n_init=2, lost_after_ms=300, expire_after_ms=1000)
    trk = ByteTracker(cfg)
    ids = set()
    for i in range(20):
        dets = [Detection((100 + 3 * i, 100, 120 + 3 * i, 140), 0.9), Detection((300, 200 - 2 * i, 318, 236 - 2 * i), 0.85)]
        out = trk.update(dets, i * 66.7)
        ids |= {t.track_id for t in out.tracks if t.state == "CONFIRMED"}
    assert len(ids) == 2
    t0 = 20 * 66.7
    states = []
    for j in range(25):
        out = trk.update([], t0 + j * 66.7)
        states.append({t.state for t in out.tracks})
        if out.expired:
            break
    assert any("LOST" in s for s in states)
    assert out.expired  # LOST -> EXPIRED after expire_after_ms


def test_tracker_epoch_in_ids_after_reset():
    trk = ByteTracker(TrackerCfg(n_init=1))
    out = trk.update([Detection((0, 0, 10, 20), 0.9)], 0)
    trk.reset()
    out2 = trk.update([Detection((0, 0, 10, 20), 0.9)], 100)
    assert out.tracks[0].track_id.endswith(":e1") and out2.tracks[0].track_id.endswith(":e2")


def _info(tid, xy, hist, vel=None, matched=True):
    return TrackInfo(tid, "CONFIRMED", matched, (0, 0, 10, 20), 0.9, xy, 0.3, hist, 10, False, vel)


def test_lock_never_switches_to_wrong_way_decoy_after_crossing():
    red = np.zeros(48, np.float32)
    red[0] = 1.0
    lk = Lock("subject", LocksCfg())
    t = 0.0
    lk.engage(_info("s:1:e1", (0.0, 0.0), red, (1.4, 0.0)), t)
    for i in range(1, 30):  # subject walks east; a decoy walking west passes within 1 m at the end
        t = i * 66.7
        x = 1.4 * t / 1000
        tracks = {"s:1:e1": _info("s:1:e1", (x, 0.0), red, (1.4, 0.0)),
                  "s:2:e1": _info("s:2:e1", (x + 0.8, 0.6), red, (-1.4, 0.0))}
        lk.update(tracks, set(), t)
    # subject's track vanishes (merged); only the decoy's new track is visible at the prediction
    for j in range(1, 15):
        t2 = t + j * 66.7
        lk.update({"s:3:e1": _info("s:3:e1", (x + 0.1, 0.3), red, (-1.4, 0.0))}, set(), t2)
    assert lk.track_id == "s:1:e1"  # decoy moving the wrong way is never adopted


def _rule_input(t, kin, **kw):
    base = dict(now_ms=t, profile="responder_safety", kin=kin, subject_state="LOCKED", subject_track="drone:1:e1",
                lock_events=[], tracks_world=[], zones=[], phone=PhoneSummary(), source_status={},
                calibration=CalibrationStatus(valid=True))
    base.update(kw)
    return RuleInput(**base)


def test_closing_fast_fires_once_per_episode_under_noise():
    eng = RuleEngine(AlertsCfg(), PhoneCfg())
    rng = np.random.default_rng(3)
    kinds = []
    for i in range(60):  # 6 s of a noisy fast approach
        c = 3.2 + rng.normal(0, 0.4)
        kin = RelativeKinematics(valid=True, range_m=12 - 0.2 * i, closing_mps=c, alignment_cos=0.95, ttc_s=None,
                                 clock=12, bearing_rel_deg=0.0, subject_speed_mps=c)
        kinds += [e.kind for e in eng.evaluate(_rule_input(i * 100.0, kin))]
    assert kinds.count("CLOSING_FAST") == 1


def test_no_closing_alert_when_responder_moves_toward_still_subject():
    eng = RuleEngine(AlertsCfg(), PhoneCfg())
    kinds = []
    for i in range(50):
        kin = RelativeKinematics(valid=True, range_m=12 - 0.25 * i, closing_mps=0.05, alignment_cos=None, clock=1,
                                 bearing_rel_deg=20.0, subject_speed_mps=0.05)
        kinds += [e.kind for e in eng.evaluate(_rule_input(i * 100.0, kin))]
    assert "CLOSING_FAST" not in kinds and "APPROACH" not in kinds


def test_lidar_proximity_independent_of_geometry_and_cloud():
    eng = RuleEngine(AlertsCfg(), PhoneCfg())
    ph = PhoneSummary(connected=True, nearest_m=0.8, nearest_sector="front", local_alert_active=True)
    evs = eng.evaluate(_rule_input(0.0, RelativeKinematics(valid=False), phone=ph,
                                   calibration=CalibrationStatus(valid=False), ai_paused=True, ai_reason="network"))
    assert any(e.kind == "LIDAR_PROXIMITY" for e in evs)
