"""Proposal -> risk kernel -> order manager, with every step in the event log."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time

from maq_brokers.base import Broker
from maq_core.clock import Clock
from maq_core.domain import Bar, OrderResult, OrderStatus, Timeframe, make_client_order_id
from maq_engine.events import DayStarted, KillSwitchExecuted
from maq_engine.orders import OrderManager
from maq_engine.store import EventStore
from maq_risk.breakers import CircuitBreakers
from maq_risk.kernel import RiskKernel
from maq_risk.kill_switch import KillSwitch
from maq_risk.models import (
    EconomicEvent,
    KillSwitchMode,
    KillSwitchState,
    Mandate,
    RiskContext,
    RiskDecision,
    RiskDecisionMade,
    TradeProposal,
)


def average_true_range(bars: Sequence[Bar], period: int = 14) -> float | None:
    """Simple average of the last ``period`` true ranges; None with too few bars."""
    if period < 1 or len(bars) < period + 1:
        return None
    ranges = [
        max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        for prev, bar in zip(bars[-period - 1 : -1], bars[-period:], strict=True)
    ]
    return sum(ranges) / period


@dataclass(frozen=True)
class PipelineResult:
    decision: RiskDecision
    decision_event_id: str
    order: OrderResult | None = None

    @property
    def executed(self) -> bool:
        return self.order is not None and self.order.status in (
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.ACCEPTED,
        )


class ExecutionPipeline:
    def __init__(
        self,
        *,
        broker: Broker,
        orders: OrderManager,
        kernel: RiskKernel,
        store: EventStore,
        clock: Clock,
        initial_balance: float,
        magic: int = 0,
        kill_switch: KillSwitch | None = None,
        breakers: CircuitBreakers | None = None,
        mandate: Mandate | None = None,
        calendar: Callable[[], Sequence[EconomicEvent]] | None = None,
        correlations: Callable[[str], dict[str, float]] | None = None,
        atr_timeframe: Timeframe = Timeframe.M15,
        atr_period: int = 14,
        max_slippage_points: int = 20,
    ) -> None:
        if initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        self.broker = broker
        self.orders = orders
        self.kernel = kernel
        self.store = store
        self.clock = clock
        self.initial_balance = initial_balance
        self.magic = magic
        self.kill_switch = kill_switch
        self.breakers = breakers
        self.mandate = mandate
        self.calendar = calendar
        self.correlations = correlations
        self.atr_timeframe = atr_timeframe
        self.atr_period = atr_period
        self.max_slippage_points = max_slippage_points
        self._flattened_for: datetime | None = None

    async def handle(self, proposal: TradeProposal) -> PipelineResult:
        """Evaluate a proposal and, if the kernel approves it, place the order."""
        await self.enforce_kill_switch()
        context = await self.build_context(proposal.symbol)
        decision = self.kernel.evaluate(proposal, context)
        [stored] = self.store.append_many(
            [
                RiskDecisionMade(
                    ts=self.clock.now(),
                    cycle_id=proposal.cycle_id,
                    proposal=proposal,
                    decision=decision,
                    equity=context.account.equity,
                    drawdown=context.drawdown,
                    daily_pnl_fraction=context.daily_pnl_fraction,
                )
            ]
        )
        decision_id = stored.event.event_id
        if not decision.approved:
            return PipelineResult(decision, decision_id)

        key = make_client_order_id(
            proposal.cycle_id or proposal.created_at.isoformat(), proposal.symbol, proposal.side
        )
        order = decision.to_order(
            key, magic=self.magic, max_slippage_points=self.max_slippage_points
        )
        result = await self.orders.submit(
            order, cycle_id=proposal.cycle_id, causation_id=decision_id
        )
        return PipelineResult(decision, decision_id, result)

    async def build_context(self, symbol: str) -> RiskContext:
        now = self.clock.now()
        account = await self.broker.account()
        spec = await self.broker.symbol_spec(symbol)
        tick = await self.broker.tick(symbol)
        positions = await self.broker.positions()
        bars = await self.broker.bars(symbol, self.atr_timeframe, self.atr_period + 1)
        day_start = self._day_start_balance(now, account.balance, account.equity)
        trades_today, volume_today = self._opened_today(now)
        return RiskContext(
            now=now,
            account=account,
            spec=spec,
            tick=tick,
            positions=positions,
            initial_balance=self.initial_balance,
            day_start_balance=day_start,
            trades_today=trades_today,
            volume_today=volume_today,
            atr=average_true_range(bars, self.atr_period),
            calendar=list(self.calendar()) if self.calendar else [],
            correlations=self.correlations(symbol) if self.correlations else {},
            kill_switch=self.kill_switch.state() if self.kill_switch else None,
            tripped_breakers=self.breakers.tripped(now) if self.breakers else [],
            mandate=self.mandate,
        )

    async def enforce_kill_switch(self) -> KillSwitchState | None:
        """Act on a FLATTEN kill switch once per engagement; returns the state."""
        if self.kill_switch is None:
            return None
        state = self.kill_switch.state()
        if (
            state is not None
            and state.mode is KillSwitchMode.FLATTEN
            and self._flattened_for != state.engaged_at
        ):
            closed, cancelled = await self.orders.flatten(f"kill switch: {state.reason}")
            self._flattened_for = state.engaged_at
            self.store.append(
                KillSwitchExecuted(
                    ts=self.clock.now(),
                    mode=state.mode,
                    reason=state.reason,
                    closed_positions=sum(1 for r in closed if r.is_filled),
                    cancelled_orders=cancelled,
                )
            )
        return state

    # ------------------------------------------------------------------ helpers
    def _day_start_balance(self, now: datetime, balance: float, equity: float) -> float:
        midnight = datetime.combine(now.date(), time(0), tzinfo=UTC)
        for event in self.store.events(kinds=[DayStarted.KIND or ""], since=midnight):
            if isinstance(event, DayStarted) and event.day == now.date():
                return event.balance
        self.store.append(DayStarted(ts=now, day=now.date(), balance=balance, equity=equity))
        return balance

    def _opened_today(self, now: datetime) -> tuple[int, float]:
        trades, volume = 0, 0.0
        for record in self.orders.records():
            result = record.result
            if (
                record.opening
                and result is not None
                and result.filled_volume > 0
                and result.time.date() == now.date()
            ):
                trades += 1
                volume += result.filled_volume
        return trades, volume
