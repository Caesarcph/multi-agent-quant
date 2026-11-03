"""Event subscriptions: ordering, overflow, closing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from maq_brokers import BrokerError, ConnectionChanged, EventHub

T0 = datetime(2026, 11, 3, 12, tzinfo=UTC)


def event(n: int) -> ConnectionChanged:
    return ConnectionChanged(ts=T0, connected=True, detail=str(n))


async def test_events_arrive_in_order_for_every_subscriber() -> None:
    hub = EventHub()
    first, second = hub.subscribe(), hub.subscribe()
    for n in range(3):
        hub.publish(event(n))
    assert [e.detail for e in first.drain()] == ["0", "1", "2"]
    assert (await second.get(timeout=1)).detail == "0"
    assert [e.detail for e in second.drain()] == ["1", "2"]


async def test_slow_subscriber_loses_the_oldest_events() -> None:
    hub = EventHub()
    slow = hub.subscribe(maxsize=2)
    for n in range(5):
        hub.publish(event(n))
    assert slow.dropped == 3
    assert [e.detail for e in slow.drain()] == ["3", "4"]


async def test_closing_ends_iteration_and_unsubscribes() -> None:
    hub = EventHub()
    sub = hub.subscribe()
    hub.publish(event(1))
    hub.close()
    received = [e.detail async for e in sub]
    assert received == ["1"]
    hub.publish(event(2))  # nobody listening any more
    assert sub.drain() == []
    with pytest.raises(BrokerError, match="closed"):
        await sub.get(timeout=1)
    sub.close()  # closing twice is fine


async def test_get_times_out() -> None:
    sub = EventHub().subscribe()
    with pytest.raises(TimeoutError):
        await sub.get(timeout=0.01)
