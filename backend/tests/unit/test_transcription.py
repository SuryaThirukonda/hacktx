import httpx
import pytest

from overwatch.config import Config
from overwatch.voice.transcription import transcribe, TranscriptionUnavailable


@pytest.mark.asyncio
async def test_transcription_uses_server_credentials_and_current_model(monkeypatch):
    cfg = Config()
    cfg.secrets.openai_api_key = "test-secret"
    original = httpx.AsyncClient
    async def handle(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = await request.aread()
        assert b"gpt-transcribe" in body and b"audio/webm" in body
        return httpx.Response(200, json={"text": "Mark the blue shirt and black pants"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    assert await transcribe(cfg, b"audio-fixture", "webm") == "Mark the blue shirt and black pants"


@pytest.mark.asyncio
async def test_transcription_limits_and_no_upstream_secret_leaks(monkeypatch):
    cfg = Config()
    with pytest.raises(TranscriptionUnavailable):
        await transcribe(cfg, b"audio", "webm")
    cfg.secrets.openai_api_key = "test-secret"
    for audio, format in [(b"", "webm"), (b"a" * (4*1024*1024+1), "webm"), (b"x", "exe")]:
        with pytest.raises(ValueError):
            await transcribe(cfg, audio, format)
    original = httpx.AsyncClient
    async def handle(request):
        return httpx.Response(401, text="secret provider body")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(TranscriptionUnavailable, match="Speech transcription failed") as err:
        await transcribe(cfg, b"audio", "mp4")
    assert "secret" not in str(err.value)
