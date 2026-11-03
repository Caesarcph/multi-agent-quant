"""The broker interface every execution venue implements.

The contract, checked for every implementation by ``maq_brokers.testing``:

* ``submit`` is idempotent on ``OrderRequest.client_order_id``: sending the same
  key again returns the earlier result (in its latest state) and never trades twice.
* A business refusal (bad volume, no margin, stops on the wrong side, ...) comes
  back as an ``OrderResult`` with status ``REJECTED`` and a ``RejectReason``.
  Exceptions are for the rest: ``BrokerConnectionError`` means the outcome of
  the call is unknown -- ask ``find_order`` before doing anything else.
* ``close_position`` is also idempotent on its ``client_order_id``.
* Management calls (``cancel``, ``modify_position``) raise ``BrokerRejection``
  when the broker refuses them.
* ``events()`` returns a subscription that sees every event from the moment it
  was created.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from maq_brokers.events import BrokerEvent
from maq_core.domain import (
    AccountState,
    Bar,
    ClosedTrade,
    OrderRequest,
    OrderResult,
    Position,
    RejectReason,
    SymbolSpec,
    Tick,
    Timeframe,
    WorkingOrder,
)

log = logging.getLogger(__name__)


# =============================================================================
# Errors
# =============================================================================
class BrokerError(Exception):
    """Base class for broker failures."""


class BrokerConnectionError(BrokerError):
    """The broker could not be reached; the outcome of the call is unknown."""


class UnknownSymbolError(BrokerError):
    def __init__(self, symbol: str) -> None:
        super().__init__(f"unknown symbol {symbol!r}")
        self.symbol = symbol


class NoPriceError(BrokerError):
    """No quote is available for the symbol (market closed, not subscribed, ...)."""


class BrokerRejection(BrokerError):
    """The broker refused a management request (cancel, modify)."""

    def __init__(self, reason: RejectReason, message: str, broker_code: str | None = None) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message
        self.broker_code = broker_code


# =============================================================================
# Event subscriptions
# =============================================================================
_CLOSED = object()


class Subscription:
    """A stream of broker events, starting at the moment it was created.

    Iterate it (``async for event in sub``), wait with ``get(timeout)``, or take
    what has arrived so far with ``drain()``. A slow consumer never blocks the
    broker: past ``maxsize`` queued events the oldest are dropped and counted.
    """

    def __init__(self, hub: EventHub, maxsize: int = 10_000) -> None:
        self._hub = hub
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._maxsize = maxsize
        self._closed = False
        self.dropped = 0

    def offer(self, event: BrokerEvent) -> None:
        if self._closed:
            return
        if self._queue.qsize() >= self._maxsize:
            self._queue.get_nowait()
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("broker event subscriber is slow; %d event(s) dropped", self.dropped)
        self._queue.put_nowait(event)

    def __aiter__(self) -> AsyncIterator[BrokerEvent]:
        return self

    async def __anext__(self) -> BrokerEvent:
        item = await self._queue.get()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)  # keep later calls ending too
            raise StopAsyncIteration
        assert isinstance(item, BrokerEvent)
        return item

    async def get(self, timeout: float | None = None) -> BrokerEvent:
        """Next event; raises ``TimeoutError`` after ``timeout`` seconds."""
        try:
            return await asyncio.wait_for(self.__anext__(), timeout)
        except StopAsyncIteration:
            raise BrokerError("subscription is closed") from None

    def drain(self) -> list[BrokerEvent]:
        """Every event that has arrived and not been consumed yet."""
        events: list[BrokerEvent] = []
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is _CLOSED:
                self._queue.put_nowait(_CLOSED)
                break
            assert isinstance(item, BrokerEvent)
            events.append(item)
        return events

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._hub.unsubscribe(self)
            self._queue.put_nowait(_CLOSED)


class EventHub:
    """Fans broker events out to every open subscription."""

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    def subscribe(self, maxsize: int = 10_000) -> Subscription:
        subscription = Subscription(self, maxsize)
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)

    def publish(self, event: BrokerEvent) -> None:
        for subscription in list(self._subscriptions):
            subscription.offer(event)

    def close(self) -> None:
        for subscription in list(self._subscriptions):
            subscription.close()


# =============================================================================
# Interfaces
# =============================================================================
@runtime_checkable
class MarketData(Protocol):
    """Read-only market access; a ``PaperBroker`` can trade on top of any of these."""

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        """Contract spec; raises ``UnknownSymbolError``."""
        ...

    async def tick(self, symbol: str) -> Tick:
        """Latest quote; raises ``NoPriceError`` when there is none."""
        ...

    async def bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        """Up to ``count`` completed bars, oldest first."""
        ...


@runtime_checkable
class Broker(MarketData, Protocol):
    @property
    def name(self) -> str: ...

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def account(self) -> AccountState: ...

    async def positions(self, symbol: str | None = None) -> list[Position]: ...

    async def working_orders(self, symbol: str | None = None) -> list[WorkingOrder]: ...

    async def closed_trades(self, since: datetime | None = None) -> list[ClosedTrade]:
        """Closed positions (or parts of them), oldest first."""
        ...

    async def submit(self, order: OrderRequest) -> OrderResult:
        """Place an order. Idempotent on ``order.client_order_id``."""
        ...

    async def find_order(self, client_order_id: str) -> OrderResult | None:
        """The latest result for an order or close request this broker has seen."""
        ...

    async def cancel(self, client_order_id: str) -> OrderResult:
        """Cancel a working order; raises ``BrokerRejection`` if there is none."""
        ...

    async def close_position(
        self, position_id: str, *, client_order_id: str, volume: float | None = None
    ) -> OrderResult:
        """Close all (or ``volume`` lots) of a position. Idempotent on ``client_order_id``.

        Raises ``BrokerRejection(NOT_FOUND)`` for a position the broker does not know.
        """
        ...

    async def modify_position(
        self, position_id: str, *, sl: float | None, tp: float | None
    ) -> Position:
        """Replace SL and TP (``None`` removes one); raises ``BrokerRejection``."""
        ...

    def events(self) -> Subscription: ...
