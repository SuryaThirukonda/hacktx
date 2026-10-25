"""Real FramePacket -> production tracker -> autonomy inputs -> visual commands, no synthetic occlusion flag."""
from dataclasses import replace

import numpy as np
import pytest

from overwatch.api.app import create_app
from overwatch.autonomy.safety import DroneSafetyState
from overwatch.config import load_config
from overwatch.sources.base import FramePacket
from overwatch.vision.detectors import Detection


class Detector:
    model_id = "measured-test"; valid_on = "any"
    boxes = [(600., 170., 740., 430.)]
    confidence = .95
    def detect(self, image):
        return [Detection(b, self.confidence) for b in self.boxes]


class Bridge:
    is_sim = False; control_capable = True; control_inhibited = False
    def connected(self, now): return True
    def send(self, command): pass


@pytest.mark.parametrize("problem", ["overlap", "low_score", "partial_box", "clipped", "missing"])
def test_pipeline_measurement_quality_stops_translation(monkeypatch, problem):
    monkeypatch.setenv("OVERWATCH_DB", ":memory:")
    cfg = load_config(); cfg.session.mode = "LIVE"; cfg.autonomy.motion_profile = "visual_follow"
    cfg.perception.cmc.enabled = False; cfg.perception.tracker.n_init = 2
    rt = create_app(cfg).state.rt; p = rt.pipeline
    detector = Detector(); p.detector = detector; p._detector_prov = ("LIVE", cfg.perception.detector)
    p.autonomy.bridge = Bridge()
    p.start_incident("quality", True)
    image = np.full((600, 1000, 3), 100, np.uint8)
    def frame(n):
        p.process_drone_frame(FramePacket("drone", n, image, n*100, n*100, "LIVE"))
    for n in range(1, 5): frame(n)
    tid = next(t.track_id for t in p.raw_tracks if t.state == "CONFIRMED")
    p.lock_track("subject", tid)
    p.autonomy.set_mode("FOLLOW", 400)
    def tick(n):
        # Supply current source time only; visibility/quality comes entirely from process_drone_frame above.
        p.counters["drone"].last_rx_ms = n*100
        inp = p._autonomy_inputs(n*100)
        inp = replace(inp, drone=DroneSafetyState(None, 2, 0, 80, True), gimbal_pitch_deg=-20)
        return inp, p.autonomy.tick(inp)[0]
    for n in range(5, 12): frame(n); inp, state = tick(n)
    assert state.last_command.right_mps > 0
    # A 20 Hz command loop cannot manufacture 5 new visual observations from one video frame.
    p.autonomy.visual_samples = 0
    for n in range(12, 16): inp, state = tick(n)
    assert p.autonomy.visual_samples == 0 and state.last_command.right_mps == 0
    if problem == "overlap": detector.boxes = [(600., 170., 740., 430.), (630., 160., 770., 440.)]
    elif problem == "low_score": detector.confidence = .2
    elif problem == "partial_box": detector.boxes = [(600., 290., 740., 430.)]
    elif problem == "clipped": detector.boxes = [(600., 170., 999., 430.)]
    else: detector.boxes = []
    frame(16); inp, state = tick(16)
    assert not inp.subject_quality_ok and inp.occluder_near_last_seen
    assert inp.subject_quality_reasons
    assert state.last_command.forward_mps == state.last_command.right_mps == state.last_command.up_mps == 0
