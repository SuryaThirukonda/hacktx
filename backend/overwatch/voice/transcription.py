"""Bounded push-to-talk transcription. Audio stays in memory; credentials stay on the backend."""
import httpx


class TranscriptionUnavailable(ValueError):
    pass


async def transcribe(cfg, audio: bytes, format: str) -> str:
    formats = {"webm": "audio/webm", "mp4": "audio/mp4", "wav": "audio/wav"}
    if format not in formats or not audio or len(audio) > 4 * 1024 * 1024:
        raise ValueError("record a short WebM, MP4 or WAV command (maximum 4 MB)")
    if not cfg.secrets.openai_api_key:
        raise TranscriptionUnavailable("OpenAI transcription is not configured; type your description instead")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response = await client.post(f"{cfg.decisions.openai_base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {cfg.secrets.openai_api_key}"},
                data={"model": cfg.voice.transcription_model},
                files={"file": (f"command.{format}", audio, formats[format])})
            response.raise_for_status()
            text = response.json().get("text", "").strip()
    except (httpx.HTTPError, ValueError, TypeError) as e:
        # Never relay upstream response bodies or authentication headers to a browser.
        raise TranscriptionUnavailable("Speech transcription failed; retry or type your description") from e
    if not text or len(text) > 500:
        raise ValueError("No short, clear command was transcribed; retry or type your description")
    return text
