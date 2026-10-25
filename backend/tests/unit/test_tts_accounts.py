import asyncio

import httpx

from overwatch.config import _elevenlabs_accounts
from overwatch.voice import tts as tts_mod


def test_account_order_follows_preference(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k1")
    monkeypatch.setenv("ELEVENLABS_API_KEY2", "k2")
    monkeypatch.setenv("ELEVENLABS_ACCOUNT", "2")
    assert [n for n, _ in _elevenlabs_accounts()] == [2, 1]
    monkeypatch.setenv("ELEVENLABS_ACCOUNT", "1")
    assert [n for n, _ in _elevenlabs_accounts()] == [1, 2]


def test_rejected_account_falls_back_and_stays_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_mod, "DISK_CACHE", tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["xi-api-key"]
        if request.url.path.endswith("/voices"):
            if key == "bad":
                return httpx.Response(401, json={"detail": "invalid"})
            return httpx.Response(200, json={"voices": [{"voice_id": "v-x", "name": "Harry - Fierce"},
                                                         {"voice_id": "v-river", "name": "River - Calm"}]})
        calls.append((key, request.url.path.rsplit("/", 1)[-1]))
        return httpx.Response(200, content=b"mp3")

    real = httpx.AsyncClient
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    t = tts_mod.ElevenLabsTTS([(2, "bad"), (1, "good")], None)
    assert asyncio.run(t.speak("one")) == b"mp3"
    assert asyncio.run(t.speak("two")) == b"mp3"
    assert calls == [("good", "v-river"), ("good", "v-river")]  # rejected key never retried; calm voice preferred
    st = t.status()
    assert st["active_account"] == 1 and st["accounts"][0]["state"] == "invalid" and st["accounts"][1]["voice"] == "River"
