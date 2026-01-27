"""Simple rate limiter for DeepSeek requests."""
from __future__ import annotations

import time
from collections import deque
from typing import Deque


class DeepSeekRateLimiter:
    def __init__(self, rpm_limit: int, safety_margin: float = 1.0) -> None:
        self._rpm_limit = max(1, int(rpm_limit))
        self._safety_margin = max(0.0, float(safety_margin))
        self._window_seconds = 60.0
        self._timestamps: Deque[float] = deque()

    @property
    def limit(self) -> int:
        return max(1, int(self._rpm_limit * self._safety_margin))

    def wait_if_needed(self) -> None:
        now = time.monotonic()
        self._trim(now)
        if len(self._timestamps) < self.limit:
            return
        earliest = self._timestamps[0]
        sleep_seconds = self._window_seconds - (now - earliest)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        now = time.monotonic()
        self._trim(now)

    def record(self) -> None:
        now = time.monotonic()
        self._trim(now)
        self._timestamps.append(now)

    def remaining(self) -> int:
        now = time.monotonic()
        self._trim(now)
        return max(self.limit - len(self._timestamps), 0)

    def _trim(self, now: float) -> None:
        while self._timestamps and (now - self._timestamps[0]) > self._window_seconds:
            self._timestamps.popleft()
