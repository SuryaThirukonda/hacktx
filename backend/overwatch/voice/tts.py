"""ElevenLabs text-to-speech (optional). Keys stay on the Mac; clients fetch audio from /api/tts.
Without a working key clients use on-device speech (browser speechSynthesis / iOS AVSpeechSynthesizer).

Several accounts can be configured (ELEVENLABS_ACCOUNT picks the preferred one). An account whose key is rejected
is disabled for the session; one that is out of quota or rate-limited is skipped for ten minutes; the next account
takes over automatically."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CRITICAL_PHRASES = ["Closing fast", "Subject lost", "Subject reacquired", "Obstacle ahead"]
# Free plans cannot use library voices over the API, so the voice comes from the account's own list (calm, clear
# premade voices first) unless ELEVENLABS_VOICE_ID is set.
PREFERRED_VOICES = ("River", "Alice", "Matilda", "Will")
DISK_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "tts"  # survives restarts: free tiers are small


@dataclass
class _Account:
    n: int
    key: str
    state: str = "untested"  # untested | ok | invalid | limited
    retry_at: float = 0.0
    error: str | None = None
    voice: str | None = None
    voice_name: str | None = None


class ElevenLabsTTS:
    def __init__(self, accounts: list[tuple[int, str]], voice_id: str | None, model: str = "eleven_flash_v2_5",
                 cache_size: int = 128) -> None:
        self.accounts = [_Account(n, k) for n, k in accounts]
        self.voice_id = voice_id  # explicit override for every account
        self.model = model
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.cache_size = cache_size
        self.errors = 0
        self.active: int | None = None

    @property
    def available(self) -> bool:
        return any(a.state != "invalid" for a in self.accounts)

    def status(self) -> dict:
        return {"active_account": self.active, "voice_override": bool(self.voice_id),
                "accounts": [{"account": a.n, "state": a.state, "error": a.error, "voice": a.voice_name}
                             for a in self.accounts]}

    async def speak(self, text: str) -> bytes | None:
        text = text.strip()[:400]
        if not text:
            return None
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        now = time.monotonic()
        for a in self.accounts:
            if a.state == "invalid" or (a.state == "limited" and now < a.retry_at):
                continue
            voice = self.voice_id or a.voice or await self._pick_voice(a)
            if voice is None:
                continue
            f = DISK_CACHE / (hashlib.sha1(f"{voice}|{self.model}|{text}".encode()).hexdigest() + ".mp3")
            if f.exists():
                self.cache[text] = f.read_bytes()
                return self.cache[text]
            audio = await self._call(a, voice, text)
            if audio is not None:
                if self.active != a.n:
                    log.info("ElevenLabs: using account %s", a.n)
                self.active = a.n
                self.cache[text] = audio
                try:
                    DISK_CACHE.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(audio)
                except OSError:
                    pass
                if len(self.cache) > self.cache_size:
                    self.cache.popitem(last=False)
                return audio
        self.active = None
        return None

    async def _pick_voice(self, a: _Account) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": a.key})
        except httpx.HTTPError as e:
            a.error = f"voices: {type(e).__name__}"
            return None
        if r.status_code in (401, 403):
            a.state, a.error = "invalid", f"HTTP {r.status_code}: key rejected"
            log.warning("ElevenLabs account %s: %s", a.n, a.error)
            return None
        voices = r.json().get("voices", []) if r.status_code == 200 else []
        if not voices:
            a.error = f"no usable voices (HTTP {r.status_code})"
            return None
        ranked = sorted(voices, key=lambda v: next((i for i, p in enumerate(PREFERRED_VOICES)
                                                     if str(v.get("name", "")).startswith(p)), len(PREFERRED_VOICES)))
        a.voice, a.voice_name = ranked[0]["voice_id"], str(ranked[0].get("name", "")).split(" - ")[0]
        return a.voice

    async def _call(self, a: _Account, voice: str, text: str) -> bytes | None:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.post(url, headers={"xi-api-key": a.key, "Accept": "audio/mpeg"},
                                 json={"text": text, "model_id": self.model})
        except httpx.HTTPError as e:
            self.errors += 1
            a.error = f"{type(e).__name__}"
            log.warning("ElevenLabs account %s request failed: %s", a.n, e)
            return None
        if r.status_code == 200:
            a.state, a.error = "ok", None
            return r.content
        self.errors += 1
        if r.status_code in (401, 403):
            a.state, a.error = "invalid", f"HTTP {r.status_code}: key rejected"
        elif "paid_plan_required" in r.text:
            a.voice, a.error = None, "voice needs a paid plan: picking another voice"
        elif r.status_code in (402, 429) or "quota" in r.text.lower():
            a.state, a.error, a.retry_at = "limited", f"HTTP {r.status_code}: quota or rate limit", time.monotonic() + 600.0
        else:
            a.error = f"HTTP {r.status_code}"
        log.warning("ElevenLabs account %s: %s", a.n, a.error)
        return None

    async def warm(self) -> None:
        """Pre-generate short critical phrases so urgent alerts never wait on synthesis."""
        for p in CRITICAL_PHRASES:
            await self.speak(p)
