"""Optional Gemini situation summaries (Overwatch §5.7). Structured facts + a few recent frames in, one factual
sentence out. The model is told not to infer intent and not to invent measurements; every summary is labelled
with its source, and the template narrator is used whenever Gemini is unavailable or slow."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROMPT = (
    "You narrate a supervised first-responder training exercise for the responder's earpiece. Using ONLY the JSON "
    "facts (measured by the system) and the attached overhead frames, write ONE short factual sentence (max 22 "
    "words) about where the tracked subject is relative to the responder and how they are moving. Never infer "
    "intent, identity, threat, or emotion. Use the given distances and clock positions; do not invent numbers. If "
    "facts say measurements are invalid, say the position is currently unknown."
)


class GeminiNarrator:
    def __init__(self, api_key: str | None, model: str = "gemini-3.8-flash") -> None:
        self.api_key = api_key
        self.model = model
        self.errors = 0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def summarize(self, facts: dict[str, Any], frames_jpeg: list[bytes]) -> str | None:
        if not self.api_key:
            return None
        parts: list[dict[str, Any]] = [{"text": PROMPT}, {"text": json.dumps(facts, default=str)}]
        for jpg in frames_jpeg[-4:]:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode("ascii")}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 80}}
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.post(url, params={"key": self.api_key}, json=body)
            if r.status_code != 200:
                self.errors += 1
                self.last_error = f"HTTP {r.status_code}"
                return None
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return " ".join(text.split())[:240]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            self.errors += 1
            self.last_error = type(e).__name__
            log.warning("Gemini summary failed: %s", e)
            return None
