"""Short, ordered, human-readable identifiers."""

from __future__ import annotations

import itertools
import secrets
import threading


class IdGen:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._it = itertools.count(1)
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            return f"{self.prefix}-{next(self._it)}"


def session_id() -> str:
    return "s-" + secrets.token_hex(3)
