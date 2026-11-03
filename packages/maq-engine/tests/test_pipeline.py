"""ExecutionPipeline end to end on a paper broker."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from maq_brokers import PaperBroker
from maq_core.clock import ManualClock
from maq_core.config.models import BreakerSettings
from maq_core.domain import Bar, OrderStatus, Timeframe
from maq_engine import (
    DayStarted,
    EventStore,
    ExecutionPipeline,
    KillSwitchExecuted,
    OrderManager,
    average_true_range,
)
from maq_risk import (
    CircuitBreakers,
    KillSwitch,
    KillSwitchMode,
    RiskDecisionMade,
    RiskKernel,
    RiskLimits,
)
from maq_risk.testing import ASK, BID, EURUSD, T0, make_proposal


def bars(count: int = 30, size: float = 0.0010) -> list[Bar]:
    start = T0 - timedelta(minutes=15 * count)
    return [
        Bar(
            time=start + timedelta(minutes=15 * i),
            open=1.1,
            high=1.1 + size / 2,
            low=1.1 - size / 2,
            close=1.1,
        )
        for i in range(count)
    ]


class Setup:
    def __init__(self, tmp_path: Path, limits: RiskLimits | None = None, **pipeline: Any) -> None:
        self.clock = ManualClock(T0)
        self.broker = PaperBroker([EURUSD], clock=self.clock)
        self.broker.set_quote("EURUSD", BID, ASK)
        self.broker.set_bars("EURUSD", Timeframe.M15, bars())
        self.store = EventStore(tmp_path / "events.db")
        self.orders = OrderManager(self.broker, self.store, self.clock, magic=42)
        self.pipeline = ExecutionPipeline(
            broker=self.broker,
            orders=self.orders,
            kernel=RiskKernel(limits or RiskLimits()),
            store=self.store,
            clock=self.clock,
            initial_balance=10_000,
            magic=42,
            **pipeline,
        )

    def kinds(self) -> list[str]:
        return [e.kind for e in self.store.events()]


@pytest.fixture(autouse=True)
def _close_stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[EventStore] = []
    original = EventStore.__init__

    def tracking_init(self: EventStore, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(EventStore, "__init__", tracking_init)
    yield
    for store in opened:
        store.close()


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


async def test_approved_proposal_becomes_a_filled_order(setup: Setup) -> None:
    outcome = await setup.pipeline.handle(make_proposal(cycle_id="cycle-1"))
    assert outcome.decision.approved, outcome.decision.reasons
    assert outcome.executed
    assert outcome.order is not None
    assert outcome.order.filled_volume == pytest.approx(0.47)
    [position] = await setup.broker.positions()
    assert position.magic == 42
    assert setup.kinds() == [
        "account.day_started",
        "risk.decision_made",
        "order.requested",
        "order.updated",
        "broker.order_filled",
    ]
    decision_event, requested = setup.store.events()[1:3]
    assert requested.causation_id == decision_event.event_id
    assert requested.cycle_id == "cycle-1"
    assert setup.store.verify().ok


async def test_the_m3_scenario_end_to_end(setup: Setup) -> None:
    reckless = await setup.pipeline.handle(
        make_proposal(stop_loss=None, requested_volume=10, cycle_id="c-1")
    )
    assert not reckless.decision.approved
    assert reckless.order is None
    sized = await setup.pipeline.handle(make_proposal(requested_volume=10, cycle_id="c-2"))
    assert sized.order is not None
    assert sized.order.filled_volume == pytest.approx(0.47)
    decisions = [e for e in setup.store.events() if isinstance(e, RiskDecisionMade)]
    assert [d.decision.approved for d in decisions] == [False, True]
    assert "stop_loss" in decisions[0].decision.reasons[0]


async def test_same_cycle_twice_places_one_order(setup: Setup) -> None:
    first = await setup.pipeline.handle(make_proposal(cycle_id="cycle-9"))
    second = await setup.pipeline.handle(make_proposal(cycle_id="cycle-9"))
    assert second.order == first.order
    assert len(await setup.broker.positions()) == 1


async def test_context_comes_from_the_broker_and_the_log(setup: Setup) -> None:
    await setup.pipeline.handle(make_proposal(cycle_id="a"))
    context = await setup.pipeline.build_context("EURUSD")
    assert context.atr == pytest.approx(0.0010)
    assert context.trades_today == 1
    assert context.volume_today == pytest.approx(0.47)
    assert len(context.positions) == 1
    assert context.day_start_balance == 10_000


async def test_day_start_is_recorded_once_per_day(setup: Setup) -> None:
    await setup.pipeline.build_context("EURUSD")
    await setup.pipeline.build_context("EURUSD")
    setup.clock.advance(days=1)
    await setup.pipeline.build_context("EURUSD")
    days = [e.day for e in setup.store.events() if isinstance(e, DayStarted)]
    assert days == [T0.date(), (T0 + timedelta(days=1)).date()]


async def test_daily_trade_limit_counts_executed_orders(tmp_path: Path) -> None:
    setup = Setup(tmp_path, RiskLimits(max_daily_trades=1))
    assert (await setup.pipeline.handle(make_proposal(cycle_id="1"))).executed
    second = await setup.pipeline.handle(make_proposal(cycle_id="2"))
    assert [c.name for c in second.decision.failed_checks] == ["max_daily_trades"]


async def test_halt_kill_switch_blocks_new_positions(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "kill.json")
    setup = Setup(tmp_path, kill_switch=switch)
    switch.engage(KillSwitchMode.HALT_NEW, "manual check", now=T0)
    outcome = await setup.pipeline.handle(make_proposal(cycle_id="k1"))
    assert [c.name for c in outcome.decision.failed_checks] == ["kill_switch"]
    switch.release()
    assert (await setup.pipeline.handle(make_proposal(cycle_id="k2"))).executed


async def test_flatten_kill_switch_closes_everything_once(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "kill.json")
    setup = Setup(tmp_path, kill_switch=switch)
    await setup.pipeline.handle(make_proposal(cycle_id="f1"))
    assert len(await setup.broker.positions()) == 1

    switch.engage(KillSwitchMode.FLATTEN, "flash crash", now=T0)
    state = await setup.pipeline.enforce_kill_switch()
    assert state is not None and state.mode is KillSwitchMode.FLATTEN
    assert await setup.broker.positions() == []
    await setup.pipeline.enforce_kill_switch()  # same engagement: nothing more to do
    executed = [e for e in setup.store.events() if isinstance(e, KillSwitchExecuted)]
    assert len(executed) == 1
    assert executed[0].closed_positions == 1


async def test_tripped_breakers_block_new_positions(tmp_path: Path) -> None:
    breakers = CircuitBreakers(BreakerSettings(min_samples=1, api_error_rate=1.0))
    setup = Setup(tmp_path, breakers=breakers)
    breakers.record_api_call(False, T0)
    outcome = await setup.pipeline.handle(make_proposal(cycle_id="b1"))
    assert [c.name for c in outcome.decision.failed_checks] == ["circuit_breakers"]
    assert outcome.order is None


async def test_price_jump_before_the_order_lands_is_refused(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = setup.broker.submit

    async def slow_submit(order: Any) -> Any:
        setup.broker.set_quote("EURUSD", 1.10060, 1.10070)  # +6 pips while in flight
        return await original(order)

    monkeypatch.setattr(setup.broker, "submit", slow_submit)
    outcome = await setup.pipeline.handle(make_proposal(cycle_id="slip"))
    assert outcome.decision.approved
    assert outcome.order is not None
    assert outcome.order.status is OrderStatus.REJECTED
    assert outcome.order.reject_reason is not None
    assert outcome.order.reject_reason.value == "PRICE_CHANGED"
    assert not outcome.executed
    assert await setup.broker.positions() == []
    assert setup.kinds()[-1] == "order.updated"


def test_average_true_range() -> None:
    assert average_true_range(bars(15), period=14) == pytest.approx(0.0010)
    assert average_true_range(bars(10), period=14) is None
    gap = [*bars(14), Bar(time=T0, open=1.2, high=1.2, low=1.2, close=1.2)]
    # The last true range spans the gap from the previous close (1.1) to 1.2.
    assert average_true_range(gap, period=14) == pytest.approx((13 * 0.0010 + 0.1) / 14)
