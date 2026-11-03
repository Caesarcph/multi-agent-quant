"""The order manager: persist first, send second, never twice.

* Every order is written to the event log (``OrderRequested``) before it goes
  to the broker. After a crash the log says which orders may have gone out.
* The ``client_order_id`` is the idempotency key. Submitting a key that is
  already known never sends again: a settled order returns its result, an
  unsettled one is looked up at the broker.
* If the connection fails during a send, the outcome is unknown. The manager
  records that, asks the broker (``find_order``), and if the broker still has
  no trace after ``not_found_grace`` it settles the order as not executed.
* ``reconcile()`` settles open questions and compares the log with the
  broker: positions we did not open are reported as orphans, and positions
  that closed while we were not watching are recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from maq_brokers.base import Broker, BrokerConnectionError, BrokerRejection
from maq_brokers.events import PositionClosed
from maq_core.clock import Clock
from maq_core.domain import (
    ClosedTrade,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    RejectReason,
    Side,
    make_client_order_id,
)
from maq_core.events import Event
from maq_engine.events import (
    IssueKind,
    OrderRequested,
    OrderSendFailed,
    OrderUpdated,
    PositionCloseRequested,
    ReconciliationIssue,
)
from maq_engine.store import EventStore

log = logging.getLogger(__name__)

# Which status may follow which; None is "requested, no answer yet".
_ANY_ANSWER = frozenset(OrderStatus) - {OrderStatus.NEW}
_TRANSITIONS: dict[OrderStatus | None, frozenset[OrderStatus]] = {
    None: _ANY_ANSWER,
    OrderStatus.NEW: _ANY_ANSWER,
    OrderStatus.SUBMITTED: _ANY_ANSWER,
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
}


@dataclass
class OrderRecord:
    """What the log says about one client order id."""

    client_order_id: str
    symbol: str
    side: Side
    volume: float
    requested_at: datetime
    opening: bool  # False for close requests
    position_id: str | None = None  # the position a close request targets
    result: OrderResult | None = None
    send_failed: bool = False

    @property
    def status(self) -> OrderStatus:
        if self.result is None:
            return OrderStatus.SUBMITTED if self.send_failed else OrderStatus.NEW
        return self.result.status

    @property
    def settled(self) -> bool:
        return self.result is not None and self.result.status.is_terminal

    @property
    def awaiting_broker(self) -> bool:
        """Requested, but whether / how the broker executed it is not known yet."""
        return self.result is None or self.result.status is OrderStatus.SUBMITTED


@dataclass
class ReconcileReport:
    settled: list[OrderResult] = field(default_factory=list)
    still_unknown: list[str] = field(default_factory=list)
    orphans: list[Position] = field(default_factory=list)
    recovered_closes: int = 0
    issues: list[ReconciliationIssue] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.still_unknown or self.orphans or self.issues)


class OrderManager:
    def __init__(
        self,
        broker: Broker,
        store: EventStore,
        clock: Clock,
        *,
        magic: int | None = None,
        not_found_grace: timedelta = timedelta(seconds=60),
    ) -> None:
        """``magic``: only positions with this magic number are ours (None: all)."""
        self._broker = broker
        self._store = store
        self._clock = clock
        self._magic = magic
        self._grace = not_found_grace
        self._orders: dict[str, OrderRecord] = {}
        self._closed_seen: set[str] = set()
        self._closed_volume: dict[str, float] = {}
        self._reported_orphans: set[str] = set()
        self._broker_events = broker.events()
        self._replay(store.events())

    # ------------------------------------------------------------------ queries
    def record(self, client_order_id: str) -> OrderRecord | None:
        return self._orders.get(client_order_id)

    def records(self) -> list[OrderRecord]:
        return list(self._orders.values())

    def open_questions(self) -> list[OrderRecord]:
        return [r for r in self._orders.values() if r.awaiting_broker]

    # ------------------------------------------------------------------ commands
    async def submit(
        self,
        order: OrderRequest,
        *,
        cycle_id: str | None = None,
        causation_id: str | None = None,
    ) -> OrderResult:
        known = self._orders.get(order.client_order_id)
        if known is not None:
            if known.awaiting_broker:
                return await self._settle(known)
            assert known.result is not None
            return known.result

        now = self._clock.now()
        self._append(
            OrderRequested(ts=now, order=order, cycle_id=cycle_id, causation_id=causation_id)
        )
        record = OrderRecord(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            volume=order.volume,
            requested_at=now,
            opening=True,
        )
        self._orders[order.client_order_id] = record
        try:
            result = await self._broker.submit(order)
        except BrokerConnectionError as error:
            return await self._send_failed(record, error, cycle_id)
        self._apply(record, result, cycle_id)
        return result

    async def close_position(
        self,
        position: Position,
        *,
        reason: str,
        volume: float | None = None,
        client_order_id: str | None = None,
        cycle_id: str | None = None,
    ) -> OrderResult:
        """Close (part of) a position. The default key makes a full close idempotent."""
        lots = volume if volume is not None else position.volume
        key = client_order_id or make_client_order_id(
            "close", position.position_id, "all" if volume is None else lots, prefix="mqc"
        )
        known = self._orders.get(key)
        if known is not None:
            if known.awaiting_broker:
                return await self._settle(known)
            assert known.result is not None
            return known.result

        now = self._clock.now()
        self._append(
            PositionCloseRequested(
                ts=now,
                client_order_id=key,
                position_id=position.position_id,
                symbol=position.symbol,
                side=position.side.opposite,
                volume=lots,
                reason=reason,
                cycle_id=cycle_id,
            )
        )
        record = OrderRecord(
            client_order_id=key,
            symbol=position.symbol,
            side=position.side.opposite,
            volume=lots,
            requested_at=now,
            opening=False,
            position_id=position.position_id,
        )
        self._orders[key] = record
        try:
            result = await self._broker.close_position(
                position.position_id, client_order_id=key, volume=volume
            )
        except BrokerConnectionError as error:
            return await self._send_failed(record, error, cycle_id)
        except BrokerRejection as error:
            result = OrderResult(
                client_order_id=key,
                status=OrderStatus.REJECTED,
                symbol=position.symbol,
                side=position.side.opposite,
                requested_volume=lots,
                time=self._clock.now(),
                reject_reason=error.reason,
                broker_code=error.broker_code,
                message=error.message,
            )
        self._apply(record, result, cycle_id)
        return result

    async def flatten(self, reason: str) -> tuple[list[OrderResult], int]:
        """Close every position of ours and cancel our working orders.

        Returns the close results and the number of cancelled orders. Positions
        that disappear meanwhile (a stop was hit) are skipped.
        """
        results: list[OrderResult] = []
        for position in await self._broker.positions():
            if self._magic is not None and position.magic != self._magic:
                continue
            result = await self.close_position(position, reason=reason)
            if result.is_rejected and result.reject_reason is not RejectReason.NOT_FOUND:
                self._issue(
                    IssueKind.CLOSE_FAILED,
                    f"could not close {position.position_id}: {result.message}",
                    client_order_id=result.client_order_id,
                    position_id=position.position_id,
                )
            results.append(result)
        cancelled = 0
        for working in await self._broker.working_orders():
            if working.client_order_id not in self._orders and self._magic is not None:
                continue
            try:
                result = await self._broker.cancel(working.client_order_id)
            except BrokerRejection:
                continue
            record = self._orders.get(working.client_order_id)
            if record is not None:
                self._apply(record, result, None)
            cancelled += 1
        self.sync_broker_events()
        return results, cancelled

    async def reconcile(self) -> ReconcileReport:
        report = ReconcileReport()
        for record in list(self._orders.values()):
            if record.awaiting_broker or record.status in (
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            ):
                before = record.result
                result = await self._settle(record)
                if result is not before and result.status is not OrderStatus.SUBMITTED:
                    report.settled.append(result)
                if record.awaiting_broker:
                    report.still_unknown.append(record.client_order_id)

        self.sync_broker_events()
        positions = await self._broker.positions()
        ours = {k for k, r in self._orders.items() if r.opening}
        for position in positions:
            if self._magic is not None and position.magic != self._magic:
                continue
            if position.client_order_id in ours:
                continue
            report.orphans.append(position)
            if position.position_id not in self._reported_orphans:
                self._reported_orphans.add(position.position_id)
                report.issues.append(
                    self._issue(
                        IssueKind.ORPHAN_POSITION,
                        f"{position.side} {position.volume} {position.symbol} "
                        f"(position {position.position_id}) was not opened by this system",
                        position_id=position.position_id,
                    )
                )

        open_ids = {p.position_id for p in positions}
        missing = [
            r
            for r in self._orders.values()
            if r.opening
            and r.result is not None
            and r.result.filled_volume > 0
            and r.result.position_id is not None
            and r.result.position_id not in open_ids
            and not self._closed_fully(r.result.position_id, r.result.filled_volume)
        ]
        if missing:
            since = min(r.requested_at for r in missing)
            trades = await self._broker.closed_trades(since=since)
            for trade in trades:
                if self._note_close(trade):
                    self._append(
                        PositionClosed(ts=self._clock.now(), trade=trade, broker="reconcile")
                    )
                    report.recovered_closes += 1
            for record in missing:
                assert record.result is not None and record.result.position_id is not None
                if not self._closed_fully(record.result.position_id, record.result.filled_volume):
                    report.issues.append(
                        self._issue(
                            IssueKind.MISSING_POSITION,
                            f"position {record.result.position_id} is gone but no close was found",
                            client_order_id=record.client_order_id,
                            position_id=record.result.position_id,
                        )
                    )
        return report

    def sync_broker_events(self) -> int:
        """Copy the broker's fills and closes into the log; returns how many."""
        events: list[Event] = []
        for event in self._broker_events.drain():
            if isinstance(event, PositionClosed) and not self._note_close(event.trade):
                continue
            events.append(event)
        if events:
            self._store.append_many(events)
        return len(events)

    def close(self) -> None:
        self._broker_events.close()

    # ------------------------------------------------------------------ internals
    async def _send_failed(
        self, record: OrderRecord, error: Exception, cycle_id: str | None
    ) -> OrderResult:
        log.warning("send of %s failed, outcome unknown: %s", record.client_order_id, error)
        self._append(
            OrderSendFailed(
                ts=self._clock.now(),
                client_order_id=record.client_order_id,
                error=str(error),
                cycle_id=cycle_id,
            )
        )
        record.send_failed = True
        return await self._settle(record)

    async def _settle(self, record: OrderRecord) -> OrderResult:
        """Ask the broker what happened; give up on the order after the grace period."""
        broker_answered = True
        try:
            found = await self._broker.find_order(record.client_order_id)
        except BrokerConnectionError as error:
            log.warning("cannot look up %s yet: %s", record.client_order_id, error)
            found, broker_answered = None, False
        if found is not None:
            if record.result is None or found != record.result:
                self._apply(record, found, None)
            return found
        if record.result is not None and not record.awaiting_broker:
            return record.result  # e.g. a working order the broker forgot: keep what we know
        now = self._clock.now()
        # Only a broker that answered "never heard of it" settles the order; a
        # failed look-up says nothing, however long it has been.
        if broker_answered and now - record.requested_at >= self._grace:
            result = OrderResult(
                client_order_id=record.client_order_id,
                status=OrderStatus.REJECTED,
                symbol=record.symbol,
                side=record.side,
                requested_volume=record.volume,
                time=now,
                reject_reason=RejectReason.NOT_FOUND,
                message="the broker has no record of this order: it was not executed",
            )
            self._apply(record, result, None)
            return result
        return OrderResult(
            client_order_id=record.client_order_id,
            status=OrderStatus.SUBMITTED,
            symbol=record.symbol,
            side=record.side,
            requested_volume=record.volume,
            time=now,
            message="outcome unknown: waiting for the broker",
        )

    def _apply(self, record: OrderRecord, result: OrderResult, cycle_id: str | None) -> None:
        previous = record.result.status if record.result is not None else None
        allowed = _TRANSITIONS.get(previous, frozenset())
        if result.status not in allowed and not (
            record.result is not None and result.status is previous
        ):
            self._issue(
                IssueKind.UNEXPECTED_TRANSITION,
                f"{record.client_order_id}: {previous} -> {result.status}",
                client_order_id=record.client_order_id,
            )
        record.result = result
        self._append(OrderUpdated(ts=self._clock.now(), result=result, cycle_id=cycle_id))
        self.sync_broker_events()

    def _issue(
        self,
        issue: IssueKind,
        detail: str,
        *,
        client_order_id: str | None = None,
        position_id: str | None = None,
    ) -> ReconciliationIssue:
        log.warning("reconciliation issue %s: %s", issue, detail)
        event = ReconciliationIssue(
            ts=self._clock.now(),
            issue=issue,
            detail=detail,
            client_order_id=client_order_id,
            position_id=position_id,
        )
        self._append(event)
        return event

    def _note_close(self, trade: ClosedTrade) -> bool:
        """Remember a close; False if it was already known."""
        key = f"{trade.position_id}@{trade.close_time.isoformat()}:{trade.volume}"
        if key in self._closed_seen:
            return False
        self._closed_seen.add(key)
        self._closed_volume[trade.position_id] = (
            self._closed_volume.get(trade.position_id, 0.0) + trade.volume
        )
        return True

    def _closed_fully(self, position_id: str, volume: float) -> bool:
        return self._closed_volume.get(position_id, 0.0) >= volume - 1e-9

    def _append(self, event: Event) -> None:
        self._store.append(event)

    def _replay(self, events: Iterable[Event]) -> None:
        """Rebuild the in-memory view from the log (after a restart)."""
        for event in events:
            if isinstance(event, OrderRequested):
                order = event.order
                self._orders[order.client_order_id] = OrderRecord(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    volume=order.volume,
                    requested_at=event.ts,
                    opening=True,
                )
            elif isinstance(event, PositionCloseRequested):
                self._orders[event.client_order_id] = OrderRecord(
                    client_order_id=event.client_order_id,
                    symbol=event.symbol,
                    side=event.side,
                    volume=event.volume,
                    requested_at=event.ts,
                    opening=False,
                    position_id=event.position_id,
                )
            elif isinstance(event, OrderSendFailed):
                if (record := self._orders.get(event.client_order_id)) is not None:
                    record.send_failed = True
            elif isinstance(event, OrderUpdated):
                if (record := self._orders.get(event.result.client_order_id)) is not None:
                    record.result = event.result
            elif isinstance(event, PositionClosed):
                self._note_close(event.trade)
            elif (
                isinstance(event, ReconciliationIssue) and event.issue is IssueKind.ORPHAN_POSITION
            ):
                if event.position_id:
                    self._reported_orphans.add(event.position_id)
