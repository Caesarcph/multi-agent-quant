"""OrderManager: persist before send, idempotency, unknown outcomes, reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any, Literal

import pytest

from maq_brokers import BrokerConnectionError, PaperBroker, PositionClosed
from maq_core.clock import ManualClock
from maq_core.domain import (
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from maq_engine import (
    EventStore,
    IssueKind,
    OrderManager,
    OrderRequested,
    OrderSendFailed,
    OrderUpdated,
    ReconciliationIssue,
)
from maq_risk.testing import ASK, BID, EURUSD, T0


class FlakyBroker:
    """A PaperBroker whose sends and look-ups can fail like a dropped connection."""

    def __init__(self, inner: PaperBroker, store: EventStore) -> None:
        self.inner = inner
        self.store = store
        self.fail_submit: Literal["before", "after"] | None = None
        self.fail_find = False
        self.submit_calls = 0
        self.persisted_first: list[bool] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def submit(self, order: OrderRequest) -> OrderResult:
        self.submit_calls += 1
        self.persisted_first.append(
            any(
                isinstance(e, OrderRequested) and e.order.client_order_id == order.client_order_id
                for e in self.store.events()
            )
        )
        if self.fail_submit == "before":
            raise BrokerConnectionError("network unreachable")
        result = await self.inner.submit(order)
        if self.fail_submit == "after":
            raise BrokerConnectionError("timed out waiting for the reply")
        return result

    async def find_order(self, client_order_id: str) -> OrderResult | None:
        if self.fail_find:
            raise BrokerConnectionError("still down")
        return await self.inner.find_order(client_order_id)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def store() -> Iterator[EventStore]:
    with EventStore() as opened:
        yield opened


@pytest.fixture
def paper(clock: ManualClock) -> PaperBroker:
    broker = PaperBroker([EURUSD], clock=clock)
    broker.set_quote("EURUSD", BID, ASK)
    return broker


@pytest.fixture
def broker(paper: PaperBroker, store: EventStore) -> FlakyBroker:
    return FlakyBroker(paper, store)


@pytest.fixture
def oms(broker: FlakyBroker, store: EventStore, clock: ManualClock) -> OrderManager:
    return OrderManager(broker, store, clock, magic=7)  # type: ignore[arg-type]


def buy(key: str = "maq-k1", volume: float = 0.1, **extra: Any) -> OrderRequest:
    return OrderRequest.model_validate(
        {"client_order_id": key, "symbol": "EURUSD", "side": "BUY", "volume": volume, "magic": 7}
        | extra
    )


def kinds(store: EventStore) -> list[str]:
    return [e.kind for e in store.events()]


class TestSubmit:
    async def test_order_is_persisted_before_it_is_sent(
        self, oms: OrderManager, broker: FlakyBroker, store: EventStore
    ) -> None:
        result = await oms.submit(buy(), cycle_id="c1")
        assert result.status is OrderStatus.FILLED
        assert broker.persisted_first == [True]
        assert kinds(store) == ["order.requested", "order.updated", "broker.order_filled"]
        requested = store.events()[0]
        assert isinstance(requested, OrderRequested)
        assert requested.cycle_id == "c1"
        assert store.verify().ok

    async def test_same_key_never_trades_twice(
        self, oms: OrderManager, broker: FlakyBroker, paper: PaperBroker
    ) -> None:
        first = await oms.submit(buy())
        again = await oms.submit(buy(volume=0.5))
        assert again == first
        assert broker.submit_calls == 1
        assert len(await paper.positions()) == 1

    async def test_rejections_are_recorded(self, oms: OrderManager, store: EventStore) -> None:
        result = await oms.submit(buy(volume=0.015))
        assert result.reject_reason is RejectReason.INVALID_VOLUME
        record = oms.record("maq-k1")
        assert record is not None and record.settled
        assert kinds(store) == ["order.requested", "order.updated"]


class TestUnknownOutcomes:
    async def test_lost_reply_after_execution_is_found(
        self, oms: OrderManager, broker: FlakyBroker, paper: PaperBroker, store: EventStore
    ) -> None:
        broker.fail_submit = "after"
        result = await oms.submit(buy())
        assert result.status is OrderStatus.FILLED  # looked up instead of guessed
        assert len(await paper.positions()) == 1
        assert "order.send_failed" in kinds(store)
        broker.fail_submit = None
        assert await oms.submit(buy()) == result
        assert broker.submit_calls == 1

    async def test_never_executed_order_is_settled_after_the_grace_period(
        self, oms: OrderManager, broker: FlakyBroker, paper: PaperBroker, clock: ManualClock
    ) -> None:
        broker.fail_submit = "before"
        pending = await oms.submit(buy())
        assert pending.status is OrderStatus.SUBMITTED
        assert "unknown" in pending.message
        assert [r.client_order_id for r in oms.open_questions()] == ["maq-k1"]

        report = await oms.reconcile()
        assert report.still_unknown == ["maq-k1"]  # too early to give up

        clock.advance(seconds=61)
        report = await oms.reconcile()
        assert report.still_unknown == []
        [settled] = report.settled
        assert settled.reject_reason is RejectReason.NOT_FOUND
        assert await paper.positions() == []
        # Resubmitting the same key does not send it again.
        broker.fail_submit = None
        assert (await oms.submit(buy())).reject_reason is RejectReason.NOT_FOUND
        assert broker.submit_calls == 1

    async def test_lookup_waits_while_the_connection_is_down(
        self, oms: OrderManager, broker: FlakyBroker, clock: ManualClock
    ) -> None:
        broker.fail_submit = "after"  # the order executed, the reply was lost
        broker.fail_find = True
        assert (await oms.submit(buy())).status is OrderStatus.SUBMITTED
        clock.advance(minutes=5)
        # Long past the grace period, but the look-up itself fails: that proves
        # nothing, so the order must stay open rather than be written off.
        report = await oms.reconcile()
        assert report.still_unknown == ["maq-k1"]
        assert report.settled == []
        broker.fail_find = False
        report = await oms.reconcile()
        assert [r.status for r in report.settled] == [OrderStatus.FILLED]


class TestRestart:
    async def test_order_persisted_but_never_sent(
        self, paper: PaperBroker, store: EventStore, clock: ManualClock
    ) -> None:
        store.append(OrderRequested(ts=clock.now(), order=buy()))  # then the process died
        oms = OrderManager(paper, store, clock)
        assert [r.client_order_id for r in oms.open_questions()] == ["maq-k1"]
        clock.advance(minutes=2)
        report = await oms.reconcile()
        assert report.settled[0].reject_reason is RejectReason.NOT_FOUND
        assert await paper.positions() == []

    async def test_order_executed_but_result_not_recorded(
        self, paper: PaperBroker, store: EventStore, clock: ManualClock
    ) -> None:
        store.append(OrderRequested(ts=clock.now(), order=buy()))
        await paper.submit(buy())  # sent, then the process died before writing the result
        oms = OrderManager(paper, store, clock)
        report = await oms.reconcile()
        assert [r.status for r in report.settled] == [OrderStatus.FILLED]
        assert report.orphans == []

    async def test_state_is_rebuilt_from_the_log(
        self, oms: OrderManager, broker: FlakyBroker, store: EventStore, clock: ManualClock
    ) -> None:
        first = await oms.submit(buy())
        restarted = OrderManager(broker, store, clock)  # type: ignore[arg-type]
        record = restarted.record("maq-k1")
        assert record is not None and record.result == first
        assert await restarted.submit(buy()) == first
        assert broker.submit_calls == 1


class TestPositions:
    async def test_close_position_is_idempotent(
        self, oms: OrderManager, paper: PaperBroker, store: EventStore
    ) -> None:
        await oms.submit(buy())
        [position] = await paper.positions()
        closed = await oms.close_position(position, reason="take profit early")
        assert closed.status is OrderStatus.FILLED
        assert closed.side is Side.SELL
        assert await oms.close_position(position, reason="again") == closed
        assert await paper.positions() == []
        assert "order.close_requested" in kinds(store)
        assert "broker.position_closed" in kinds(store)

    async def test_close_of_a_vanished_position_is_a_rejection(
        self, oms: OrderManager, paper: PaperBroker
    ) -> None:
        await oms.submit(buy())
        [position] = await paper.positions()
        await paper.close_position(position.position_id, client_order_id="manual-close")
        result = await oms.close_position(position, reason="late")
        assert result.reject_reason is RejectReason.NOT_FOUND

    async def test_flatten_closes_ours_and_cancels_working_orders(
        self, oms: OrderManager, paper: PaperBroker
    ) -> None:
        await oms.submit(buy("maq-a"))
        await oms.submit(buy("maq-b", type=OrderType.LIMIT, price=1.0950))
        await paper.submit(buy("manual", magic=0))  # opened by hand, magic 0
        closed, cancelled = await oms.flatten("kill switch")
        assert [r.status for r in closed] == [OrderStatus.FILLED]
        assert cancelled == 1
        [left] = await paper.positions()
        assert left.client_order_id == "manual"
        record = oms.record("maq-b")
        assert record is not None and record.status is OrderStatus.CANCELLED


class TestReconcile:
    async def test_orphans_are_reported_once(
        self, oms: OrderManager, paper: PaperBroker, store: EventStore
    ) -> None:
        await oms.submit(buy())
        await paper.submit(buy("stray", magic=7))  # ours by magic, unknown to the log
        report = await oms.reconcile()
        assert [p.client_order_id for p in report.orphans] == ["stray"]
        assert [i.issue for i in report.issues] == [IssueKind.ORPHAN_POSITION]
        again = await oms.reconcile()
        assert [p.client_order_id for p in again.orphans] == ["stray"]
        assert again.issues == []  # not logged a second time
        issues = [e for e in store.events() if isinstance(e, ReconciliationIssue)]
        assert len(issues) == 1

    async def test_closes_missed_while_down_are_recovered(
        self,
        broker: FlakyBroker,
        paper: PaperBroker,
        store: EventStore,
        clock: ManualClock,
    ) -> None:
        first = OrderManager(broker, store, clock)  # type: ignore[arg-type]
        await first.submit(buy(sl=1.0980))
        first.close()  # the process stops listening
        paper.set_quote("EURUSD", 1.0970, 1.0971)  # stop hit while nobody watches
        clock.advance(minutes=1)

        restarted = OrderManager(broker, store, clock)  # type: ignore[arg-type]
        report = await restarted.reconcile()
        assert report.recovered_closes == 1
        assert report.clean
        closes = [e for e in store.events() if isinstance(e, PositionClosed)]
        assert [c.trade.reason.value for c in closes] == ["STOP_LOSS"]
        assert (await restarted.reconcile()).recovered_closes == 0

    async def test_position_gone_without_a_trace(
        self,
        oms: OrderManager,
        paper: PaperBroker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await oms.submit(buy())
        oms.close()  # stop listening so the close below is not seen
        [position] = await paper.positions()
        await paper.close_position(position.position_id, client_order_id="elsewhere")

        async def no_history(since: Any = None) -> list[Any]:
            return []

        monkeypatch.setattr(paper, "closed_trades", no_history)
        report = await oms.reconcile()
        assert [i.issue for i in report.issues] == [IssueKind.MISSING_POSITION]

    async def test_unexpected_transition_is_flagged(
        self, oms: OrderManager, paper: PaperBroker, store: EventStore
    ) -> None:
        filled = await oms.submit(buy())
        record = oms.record("maq-k1")
        assert record is not None
        weird = filled.model_copy(update={"status": OrderStatus.CANCELLED, "filled_volume": 0.0})
        oms._apply(record, weird, None)
        issues = [e for e in store.events() if isinstance(e, ReconciliationIssue)]
        assert [i.issue for i in issues] == [IssueKind.UNEXPECTED_TRANSITION]
        assert "FILLED -> CANCELLED" in issues[0].detail


async def test_events_are_typed(oms: OrderManager, store: EventStore) -> None:
    await oms.submit(buy())
    events = store.events()
    assert isinstance(events[0], OrderRequested)
    assert isinstance(events[1], OrderUpdated)
    assert not any(isinstance(e, OrderSendFailed) for e in events)
    assert timedelta(0) <= events[1].ts - events[0].ts


class TestEdges:
    async def test_close_with_a_lost_reply_is_looked_up(
        self, oms: OrderManager, broker: FlakyBroker, paper: PaperBroker
    ) -> None:
        await oms.submit(buy())
        [position] = await paper.positions()
        original = paper.close_position

        async def close_then_drop(*args: Any, **kwargs: Any) -> OrderResult:
            await original(*args, **kwargs)
            raise BrokerConnectionError("reply lost")

        paper.close_position = close_then_drop  # type: ignore[method-assign]
        result = await oms.close_position(position, reason="exit")
        assert result.status is OrderStatus.FILLED
        assert await paper.positions() == []
        # Asking again settles from the log without another close.
        assert await oms.close_position(position, reason="exit") == result

    async def test_status_of_an_unanswered_order(
        self, oms: OrderManager, broker: FlakyBroker
    ) -> None:
        broker.fail_submit = "before"
        await oms.submit(buy())
        record = oms.record("maq-k1")
        assert record is not None
        assert record.status is OrderStatus.SUBMITTED
        assert not record.settled
        # Resubmitting an unsettled key looks it up instead of sending again.
        broker.fail_submit = None
        assert (await oms.submit(buy())).status is OrderStatus.SUBMITTED
        assert broker.submit_calls == 1

    async def test_flatten_leaves_foreign_working_orders(
        self, oms: OrderManager, paper: PaperBroker
    ) -> None:
        await paper.submit(buy("foreign", type=OrderType.LIMIT, price=1.0950))
        closed, cancelled = await oms.flatten("stop")
        assert (closed, cancelled) == ([], 0)
        assert len(await paper.working_orders()) == 1
