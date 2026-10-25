import sys

from overwatch.config import TrackerCfg, load_config
from overwatch.vision.detectors import Detection, NullDetector, SimBlobDetector, build_detector
from overwatch.vision.tracker import ByteTracker


def test_short_gap_keeps_the_same_track_id():
    trk = ByteTracker(TrackerCfg(n_init=2, lost_after_ms=300, expire_after_ms=2000))
    first = None
    for i in range(10):
        out = trk.update([Detection((100 + 4 * i, 100, 120 + 4 * i, 140), 0.9)], i * 66.7)
        conf = [t for t in out.tracks if t.state == "CONFIRMED"]
        if conf and first is None:
            first = conf[0].track_id
    for j in range(5):  # ~330 ms with no detection: lost, not expired
        out = trk.update([], (10 + j) * 66.7)
    assert any(t.state == "LOST" for t in out.tracks)
    out = trk.update([Detection((100 + 4 * 15, 100, 120 + 4 * 15, 140), 0.9)], 15 * 66.7)
    assert first is not None and any(t.track_id == first and t.state == "CONFIRMED" for t in out.tracks)


def test_missing_model_falls_back_honestly(monkeypatch):
    cfg = load_config()
    monkeypatch.setitem(sys.modules, "ultralytics", None)  # import fails as if not installed
    d = build_detector("ultralytics", "LIVE", cfg)
    assert isinstance(d, NullDetector) and d.model_id.startswith("none (ultralytics unavailable")
    assert d.detect(None) == []  # no detections, never invented ones


def test_auto_uses_sim_detector_only_on_synthetic_frames(monkeypatch):
    cfg = load_config()
    d = build_detector("auto", "SYNTHETIC", cfg)
    assert isinstance(getattr(d, "base", d), SimBlobDetector) or d.model_id.startswith("sim-blob")
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    assert build_detector("auto", "LIVE", cfg).model_id.startswith("none (ultralytics")
