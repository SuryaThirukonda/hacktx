import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from overwatch.pipeline import SourceCounters
from overwatch.sources.live import URLFrameSource
from overwatch.sources.video import SourceError, VideoSourceManager, classify_url


class FakeClock:
    def now_ms(self) -> float:
        return time.monotonic() * 1000.0


class FakePipeline:
    def __init__(self) -> None:
        self.counters = {"drone": SourceCounters()}
        self.frames = []

    def note_dropped(self, src):
        self.counters[src].dropped += 1

    def process_drone_frame(self, pkt):
        self.frames.append(pkt)
        self.counters["drone"].last_rx_ms = pkt.receive_ts_ms


def fake_rt(mode="LIVE", live=None):
    return SimpleNamespace(clock=FakeClock(), pipeline=FakePipeline(), mode=mode, live=live, sim=None,
                           cfg=SimpleNamespace(sources=SimpleNamespace(drone=SimpleNamespace(url=None))))


def write_clip(path, n=12, fps=10.0):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(n):
        vw.write(np.full((48, 64, 3), i * 15, np.uint8))
    vw.release()


def test_classify_url():
    assert classify_url("0") == "webcam" and classify_url("rtsp://cam/1") == "rtsp"
    assert classify_url("/tmp/clip.mp4") == "file"


def test_file_source_uses_media_timestamps_and_replay_provenance(tmp_path):
    clip = tmp_path / "clip.mp4"
    write_clip(clip)
    rt = fake_rt()
    src = URLFrameSource(rt, str(clip))
    src.loop_file = False
    src.start()
    deadline = time.monotonic() + 5
    while not src.finished and time.monotonic() < deadline:
        time.sleep(0.05)
    src.stop()
    frames = rt.pipeline.frames
    assert src.finished and len(frames) >= 8
    assert {f.provenance for f in frames} == {"RECORDED"} and {f.timestamp_quality for f in frames} == {"media"}
    ts = [f.capture_ts_ms for f in frames]
    assert ts == sorted(ts)
    gaps = np.diff(ts)
    assert np.median(gaps) == pytest.approx(100.0, abs=15.0)  # 10 fps media time, not arrival jitter


def test_unreachable_stream_counts_reconnects_without_frames():
    rt = fake_rt()
    src = URLFrameSource(rt, "rtsp://127.0.0.1:9/none")
    src.retry_s = 0.05
    src.start()
    deadline = time.monotonic() + 8
    while src.reconnects < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    src.stop()
    assert src.reconnects >= 2 and rt.pipeline.counters["drone"].reconnects >= 2
    assert rt.pipeline.frames == []


def test_simulated_backend_refuses_real_video_honestly():
    rt = fake_rt(mode="SIMULATED")
    rt.sim = SimpleNamespace(scenario=SimpleNamespace(id="DEMO-1"))
    m = VideoSourceManager(rt)
    assert m.active == "simulated" and m.status("simulated").connection_status == "connected"
    with pytest.raises(SourceError, match="LIVE mode"):
        m.select("file", "/tmp/x.mp4")
    dji = m.status("dji_mini2")
    assert dji.connection_status == "not_configured" and "not verified" in dji.detail


def test_dji_bridge_is_never_reported_live_without_a_connection():
    bridge = SimpleNamespace(last_rx_ms=-1e12, synthetic=False, connected=lambda now: False)
    rt = fake_rt(mode="LIVE", live=SimpleNamespace(bridge=bridge, url_source=None))
    m = VideoSourceManager(rt)
    s = m.status("dji_mini2")
    assert s.connection_status == "disconnected" and s.last_seen_at is None
    bridge.connected = lambda now: True
    bridge.last_rx_ms = rt.clock.now_ms()
    bridge.synthetic = True  # the fake_devices harness
    s = m.status("dji_mini2")
    assert s.connection_status == "connected" and s.source_mode == "simulated"


def test_file_selection_checks_the_path(tmp_path):
    rt = fake_rt(mode="LIVE", live=SimpleNamespace(bridge=None, url_source=None))
    m = VideoSourceManager(rt)
    with pytest.raises(SourceError, match="not found"):
        m.select("file", str(tmp_path / "missing.mp4"))
    with pytest.raises(SourceError, match="looks like"):
        m.select("file", "rtsp://cam/1")
