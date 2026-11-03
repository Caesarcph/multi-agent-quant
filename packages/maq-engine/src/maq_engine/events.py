"""Events the engine writes to the log."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from maq_core.domain import Lots, OrderRequest, OrderResult, Side, Symbol
from maq_core.events import Event
from maq_risk.models import KillSwitchMode


class OrderRequested(Event):
    """Written before the order is sent: after a crash we know it may have gone out."""

    kind: Literal["order.requested"] = "order.requested"
    order: OrderRequest


class PositionCloseRequested(Event):
    kind: Literal["order.close_requested"] = "order.close_requested"
    client_order_id: str
    position_id: str
    symbol: Symbol
    side: Side  # side of the closing deal
    volume: Lots
    reason: str


class OrderSendFailed(Event):
    """The connection failed during a send: the outcome is unknown until reconciled."""

    kind: Literal["order.send_failed"] = "order.send_failed"
    client_order_id: str
    error: str


class OrderUpdated(Event):
    kind: Literal["order.updated"] = "order.updated"
    result: OrderResult


class IssueKind(StrEnum):
    ORPHAN_POSITION = "orphan_position"  # at the broker, never opened by us
    MISSING_POSITION = "missing_position"  # we opened it, the broker has no trace of it
    UNEXPECTED_TRANSITION = "unexpected_transition"
    CLOSE_FAILED = "close_failed"


class ReconciliationIssue(Event):
    kind: Literal["reconcile.issue"] = "reconcile.issue"
    issue: IssueKind
    detail: str
    client_order_id: str | None = None
    position_id: str | None = None


class DayStarted(Event):
    """Balance at the first look of a UTC day: the reference for the daily loss limit."""

    kind: Literal["account.day_started"] = "account.day_started"
    day: date
    balance: float
    equity: float


class KillSwitchExecuted(Event):
    kind: Literal["risk.kill_switch_executed"] = "risk.kill_switch_executed"
    mode: KillSwitchMode
    reason: str
    closed_positions: int
    cancelled_orders: int
