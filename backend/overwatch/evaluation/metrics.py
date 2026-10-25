"""In-memory metrics: counters, rolling latency percentiles, event rates. Every number keeps its denominator."""

from __future__ import annotations

import threading
from collections import defaultdict, deque

import numpy as np


class LatencyWindow:
    def __init__(self, maxlen: int = 300) -> None:
        self.values: deque[float] = deque(maxlen=maxlen)
        self.total = 0

    def add(self, v: float) -> None:
        self.values.append(float(v))
        self.total += 1

    def summary(self) -> dict[str, float | None]:
        if not self.values:
            return {"n": 0, "p50": None, "p95": None, "max": None, "last": None}
        arr = np.fromiter(self.values, dtype=float)
        return {
            "n": float(self.total),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "max": float(arr.max()),
            "last": float(arr[-1]),
        }


class RateMeter:
    """Events per second over a sliding window, driven by the pipeline clock."""

    def __init__(self, window_ms: float = 3000.0) -> None:
        self.window_ms = window_ms
        self.stamps: deque[float] = deque()

    def tick(self, now_ms: float) -> None:
        self.stamps.append(now_ms)
        self._trim(now_ms)

    def rate(self, now_ms: float) -> float:
        self._trim(now_ms)
        if len(self.stamps) < 2:
            return 0.0
        span = max(now_ms - self.stamps[0], 1.0)
        return 1000.0 * (len(self.stamps) - 1) / span if span > 0 else 0.0

    def _trim(self, now_ms: float) -> None:
        while self.stamps and now_ms - self.stamps[0] > self.window_ms:
            self.stamps.popleft()


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, float] = defaultdict(float)
        self.latencies: dict[str, LatencyWindow] = defaultdict(LatencyWindow)
        self.rates: dict[str, RateMeter] = defaultdict(RateMeter)
        self.series: dict[str, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=240))

    def inc(self, name: str, by: float = 1.0) -> None:
        with self._lock:
            self.counters[name] += by

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            self.latencies[name].add(value_ms)

    def tick(self, name: str, now_ms: float) -> None:
        with self._lock:
            self.rates[name].tick(now_ms)

    def record_series(self, name: str, now_ms: float, value: float) -> None:
        with self._lock:
            self.series[name].append((now_ms, float(value)))

    def snapshot(self, now_ms: float) -> dict:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "latencies": {k: v.summary() for k, v in self.latencies.items()},
                "rates": {k: round(v.rate(now_ms), 2) for k, v in self.rates.items()},
            }

    def series_snapshot(self) -> dict[str, list[tuple[float, float]]]:
        with self._lock:
            return {k: list(v) for k, v in self.series.items()}

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.latencies.clear()
            self.rates.clear()
            self.series.clear()
