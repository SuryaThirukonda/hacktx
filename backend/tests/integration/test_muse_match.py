"""Mocked contract/async safety tests. These are not evidence of a live Muse call."""
import threading
import time

import pytest

from .test_target_acquisition import setup_scene


def wait_result(rt, iid):
    for _ in range(100):
        state = rt.incidents.person_mark.view(iid)
        if state['status'] != 'pending':
            return state
        time.sleep(.01)
    raise AssertionError('background result timed out')


def test_missing_key_uses_existing_continuous_rules(monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    c, rt, iid, frame = setup_scene(monkeypatch)
    state = c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'}).json()
    assert state['engine'] == 'clothing_rules_fallback'
    for n in (7, 8, 9):
        frame(n)
        rt.incidents.targets.update()
    state = rt.incidents.person_mark.view(iid)
    assert state['status'] == 'approved' and 'Fallback' in state['reason']
    assert rt.incidents.targets.view(iid)['subject']
    assert not state.get('human_confirmed')
    assert rt.pipeline.autonomy.mode == 'MANUAL'


@pytest.mark.parametrize('mode', ['SIMULATED', 'LIVE'])
def test_muse_candidate_reuses_memory_and_requires_live_confirmation(monkeypatch, mode):
    monkeypatch.setenv('MODEL_API_KEY', 'test-not-a-real-key')
    c, rt, iid, frame = setup_scene(monkeypatch, mode=mode)
    tid = rt.pipeline.raw_tracks[0].track_id
    def select(description, images):
        assert tid in images and images[tid].startswith(b'\xff\xd8')
        return {'candidate_id': tid, 'confidence': .97}
    monkeypatch.setattr('overwatch.incident.muse.select_candidate', select)
    state = c.post(f'/api/incidents/{iid}/person-mark', json={'text': 'red hoodie'}).json()
    state = wait_result(rt, iid)
    assert state['engine'] == 'muse' and state['track_id'] == tid
    if mode == 'LIVE':
        assert state['status'] == 'review'
        assert not rt.pipeline.locks['subject'].active
        result = c.post(f'/api/incidents/{iid}/person-mark/confirm', json={'request_id': state['request_id']})
        assert result.status_code == 200, result.text
    else:
        assert state['status'] == 'approved' and not state['human_confirmed']
    frame(7)
    rt.incidents.targets.update()
    assert rt.incidents.targets.view(iid)['subject']['last_track_id'] == tid
    assert rt.pipeline.autonomy.mode == 'MANUAL'
    assert not rt.cfg.autonomy.allow_real_flight
    if mode == 'SIMULATED':
        assert rt.pipeline.flight_confirmed_track_id is None


@pytest.mark.parametrize('change', ['stale', 'stale_capture', 'session', 'incident', 'lost', 'cancel', 'lock', 'epoch'])
def test_inflight_result_cannot_override_changed_evidence(monkeypatch, change):
    monkeypatch.setenv('MODEL_API_KEY', 'test-not-a-real-key')
    c, rt, iid, frame = setup_scene(monkeypatch)
    tid = rt.pipeline.raw_tracks[0].track_id
    started, finish = threading.Event(), threading.Event()
    def select(*args):
        started.set()
        assert finish.wait(2)
        return {'candidate_id': tid, 'confidence': .99}
    monkeypatch.setattr('overwatch.incident.muse.select_candidate', select)
    c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'})
    assert started.wait(1)
    assert c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'}).status_code == 409
    p = rt.pipeline
    if change == 'stale': rt.clock.set_ms(100000)
    elif change == 'stale_capture': p.counters['drone'].last_capture_ms = -10000
    elif change == 'incident': rt.incidents.current_id = 'replaced-incident'
    elif change == 'session': p.session_id = 'new-session'
    elif change == 'lost': p.raw_tracks.clear()
    elif change == 'cancel': rt.incidents.person_mark.release(iid)
    elif change == 'lock': p.lock_track('subject', p.raw_tracks[1].track_id)
    elif change == 'epoch': p.tracker.epoch += 1
    finish.set()
    state = wait_result(rt, iid)
    assert state['status'] not in ('approved', 'review')
    assert p.autonomy.mode == 'MANUAL'


def test_api_failure_falls_back_and_none_does_not_select(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'test-not-a-real-key')
    c, rt, iid, frame = setup_scene(monkeypatch)
    def fail(*args): raise RuntimeError('private response must not be exposed')
    monkeypatch.setattr('overwatch.incident.muse.select_candidate', fail)
    c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'})
    state = wait_result(rt, iid)
    assert state['engine'] == 'clothing_rules_fallback' and state['continuous']
    assert 'private' not in str(state)
    monkeypatch.setattr('overwatch.incident.muse.select_candidate', lambda *args: {'candidate_id': None, 'confidence': .2})
    c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'})
    assert wait_result(rt, iid)['status'] == 'no_match'
    assert not rt.pipeline.locks['subject'].active


def test_muse_network_latency_does_not_use_flight_decision_expiry(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'test-not-a-real-key')
    c, rt, iid, frame = setup_scene(monkeypatch)
    tid = rt.pipeline.raw_tracks[0].track_id
    started, finish = threading.Event(), threading.Event()
    def select(*args):
        started.set()
        assert finish.wait(2)
        return {'candidate_id': tid, 'confidence': .99}
    monkeypatch.setattr('overwatch.incident.muse.select_candidate', select)
    c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'})
    assert started.wait(1)
    frame(30)  # 2.4 s elapsed, beyond generic decision expiry, with a new current frame
    finish.set()
    assert wait_result(rt, iid)['status'] == 'approved'
    assert rt.pipeline.autonomy.mode == 'MANUAL'


def test_muse_rejects_stale_capture_before_replacing_current_mark(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'test-not-a-real-key')
    c, rt, iid, frame = setup_scene(monkeypatch)
    rt.pipeline.counters['drone'].last_capture_ms = -10000
    response = c.post(f'/api/incidents/{iid}/target/search', json={'description': 'red shirt'})
    assert response.status_code == 409
    assert rt.incidents.person_mark.view(iid)['status'] == 'idle'
