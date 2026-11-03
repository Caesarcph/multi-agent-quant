"""Events a broker reports on its own: fills, closed positions, cancellations."""

from __future__ import annotations

from typing import Literal

from maq_core.domain import ClosedTrade, Lots, Price, Side, Symbol
from maq_core.events import Event


class BrokerEvent(Event):
    """Something that happened at a broker (abstract grouping)."""

    broker: str = ""


class OrderFilled(BrokerEvent):
    """A deal: an order (or a close request) was executed."""

    kind: Literal["broker.order_filled"] = "broker.order_filled"
    client_order_id: str | None  # None when the broker closed the position (SL, TP, stop-out)
    position_id: str
    symbol: Symbol
    side: Side  # side of the deal: a SELL deal closes a long position
    volume: Lots
    price: Price
    commission: float = 0.0
    opening: bool = True  # False when the deal reduced or closed a position


class PositionClosed(BrokerEvent):
    """A position, or part of it, was closed -- by us, by SL/TP or by the broker."""

    kind: Literal["broker.position_closed"] = "broker.position_closed"
    trade: ClosedTrade


class OrderCancelled(BrokerEvent):
    """A working (LIMIT / STOP) order went away without being filled."""

    kind: Literal["broker.order_cancelled"] = "broker.order_cancelled"
    client_order_id: str
    symbol: Symbol
    expired: bool = False


class ConnectionChanged(BrokerEvent):
    kind: Literal["broker.connection_changed"] = "broker.connection_changed"
    connected: bool
    detail: str = ""
