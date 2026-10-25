"""Clocks. All pipeline code takes time from an injected clock so the same code runs paced or headless."""

from __future__ import annotations

import statistics
import time
from collections import deque


class Clock:
    """Pipeline clock in milliseconds (monotonic)."""

    def now_ms(self) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class WallClock(Clock):
    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0


class SimClock(Clock):
    """Manually advanced clock used by the simulator (pauses when the simulation pauses)."""

    def __init__(self, start_ms: float = 0.0) -> None:
        self._t = start_ms

    def now_ms(self) -> float:
        return self._t

    def set_ms(self, t_ms: float) -> None:
        self._t = max(self._t, t_ms)

    def advance_ms(self, dt_ms: float) -> None:
        self._t += dt_ms

    def reset_to(self, t_ms: float = 0.0) -> None:
        """Explicit rewind for a new simulation session (scenario restart)."""
        self._t = t_ms


class OffsetEstimator:
    """NTP-style offset between a device clock and the server clock.

    Each ping round trip gives t0 (device send), t1 (server receive), t2 (server send), t3 (device receive).
    offset = ((t1 - t0) + (t2 - t3)) / 2, rtt = (t3 - t0) - (t2 - t1). The estimate with the minimum RTT in the
    window is the most trustworthy; jitter is reported so sync tolerance can be chosen from data.
    """

    def __init__(self, window: int = 32) -> None:
        self.samples: deque[tuple[float, float]] = deque(maxlen=window)  # (rtt, offset)

    def add(self, t0: float, t1: float, t2: float, t3: float) -> None:
        rtt = (t3 - t0) - (t2 - t1)
        offset = ((t1 - t0) + (t2 - t3)) / 2.0
        if rtt >= 0:
            self.samples.append((rtt, offset))

    @property
    def offset_ms(self) -> float | None:
        if not self.samples:
            return None
        return min(self.samples)[1]

    @property
    def min_rtt_ms(self) -> float | None:
        return min(self.samples)[0] if self.samples else None

    @property
    def jitter_ms(self) -> float | None:
        if len(self.samples) < 3:
            return None
        return statistics.pstdev(o for _, o in self.samples)

    def to_server(self, device_ts_ms: float) -> float | None:
        off = self.offset_ms
        return None if off is None else device_ts_ms + off
