"""maq_core.clock and maq_core.events."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from pydantic import ValidationError

from maq_core.clock import Clock, ManualClock, SystemClock
from maq_core.domain import Side
from maq_core.events import (
    Event,
    UnknownEventError,
    canonical_json,
    event_types,
    new_event_id,
    parse_event,
)

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class SampleHappened(Event):
    kind: Literal["test.sample_happened"] = "test.sample_happened"
    side: Side
    value: float


class AbstractGroup(Event):
    """No kind default: a grouping, not a concrete event."""

    group_field: int = 0


class TestClocks:
    def test_system_clock_is_aware_utc(self) -> None:
        now = SystemClock().now()
        assert now.tzinfo is UTC
        assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)

    def test_both_satisfy_the_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)
        assert isinstance(ManualClock(T0), Clock)

    def test_manual_clock_moves_only_forward(self) -> None:
        clock = ManualClock(T0)
        assert clock.now() == T0
        assert clock.advance(minutes=15) == T0 + timedelta(minutes=15)
        assert clock.advance(timedelta(seconds=30)) == T0 + timedelta(minutes=15, seconds=30)
        clock.set(T0 + timedelta(hours=1))
        assert clock.now() == T0 + timedelta(hours=1)
        with pytest.raises(ValueError, match="backwards"):
            clock.advance(timedelta(seconds=-1))
        with pytest.raises(ValueError, match="back"):
            clock.set(T0)

    def test_manual_clock_needs_aware_times(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            ManualClock(datetime(2026, 11, 3))
        assert "2026-11-03" in repr(ManualClock(T0))


class TestEvents:
    def test_subclasses_register_by_kind(self) -> None:
        assert event_types()["test.sample_happened"] is SampleHappened
        assert SampleHappened.KIND == "test.sample_happened"
        assert AbstractGroup.KIND is None
        assert "test.abstract" not in event_types()

    def test_round_trip_through_canonical_json(self) -> None:
        event = SampleHappened(ts=T0, side=Side.SELL, value=1.5, cycle_id="c1")
        text = canonical_json(event)
        assert json.loads(text)["kind"] == "test.sample_happened"
        # Sorted keys, no whitespace: the same event always gives the same text.
        assert text == canonical_json(parse_event(text))
        assert ", " not in text
        restored = parse_event(text)
        assert isinstance(restored, SampleHappened)
        assert restored == event
        assert parse_event(json.loads(text)) == event

    def test_unknown_kind(self) -> None:
        with pytest.raises(UnknownEventError, match="nope"):
            parse_event({"kind": "nope", "ts": T0.isoformat()})
        with pytest.raises(UnknownEventError):
            parse_event({"ts": T0.isoformat()})
        with pytest.raises(ValueError, match="JSON object"):
            parse_event("[1, 2]")

    def test_duplicate_kind_is_refused(self) -> None:
        with pytest.raises(TypeError, match="defined twice"):

            class Impostor(Event):
                kind: Literal["test.sample_happened"] = "test.sample_happened"

    def test_events_are_immutable_and_strict(self) -> None:
        event = SampleHappened(ts=T0, side=Side.BUY, value=1.0)
        with pytest.raises(ValidationError):
            event.value = 2.0  # type: ignore[misc]
        with pytest.raises(ValidationError):
            SampleHappened(ts=T0, side=Side.BUY, value=float("nan"))
        with pytest.raises(ValidationError):
            SampleHappened.model_validate(
                {"ts": T0, "side": "BUY", "value": 1.0, "unexpected": True}
            )

    def test_event_ids_are_unique_and_time_ordered(self) -> None:
        ids = [new_event_id() for _ in range(200)]
        assert len(set(ids)) == len(ids)
        assert all(len(i) == 32 for i in ids)
        # The first 12 hex digits are the millisecond timestamp.
        stamps = [int(i[:12], 16) for i in ids]
        assert stamps == sorted(stamps)
        assert ids[0][12] == "7"  # UUID version 7
