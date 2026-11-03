"""Clocks: the only place the platform asks what time it is.

Live code gets a ``SystemClock``; backtests, replays and tests get a
``ManualClock`` that only moves when told to. Code that calls
``datetime.now()`` directly cannot be replayed, so new code takes a ``Clock``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...


class SystemClock:
    """Wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemClock()"


class ManualClock:
    """A clock that stands still until it is advanced. Time never goes backwards."""

    def __init__(self, start: datetime) -> None:
        self._now = _require_aware(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta | None = None, /, **kwargs: float) -> datetime:
        """Move forward by ``delta`` or by ``timedelta(**kwargs)``; returns the new time."""
        step = delta if delta is not None else timedelta(**kwargs)
        if step < timedelta(0):
            raise ValueError("a ManualClock cannot go backwards")
        self._now += step
        return self._now

    def set(self, when: datetime) -> None:
        when = _require_aware(when)
        if when < self._now:
            raise ValueError(f"cannot move the clock back from {self._now} to {when}")
        self._now = when

    def __repr__(self) -> str:
        return f"ManualClock({self._now.isoformat()})"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock times must be timezone-aware")
    return value.astimezone(UTC)
