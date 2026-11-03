"""A local matching engine for paper trading on live or replayed prices.

What it simulates:

* market orders fill at the ask (BUY) or bid (SELL), plus adverse slippage;
* LIMIT orders fill at their price or better, STOP orders at the market once
  triggered (with slippage);
* a stop loss fills at the market, so a gap through it costs more than the stop;
  a take profit fills exactly at its level (no favourable slippage);
* commission per lot and side, swap at the daily rollover (triple on Wednesday);
* margin (FX formula, see ``SymbolSpec.margin``), ``NO_MONEY`` rejections and a
  stop-out that closes the worst position while the margin level is too low;
* a hedging account: every fill opens its own position.

Not simulated: partial fills, trading sessions, and currency conversion beyond
the ``tick_value`` snapshot in each ``SymbolSpec``.

Prices come from ``feed_tick`` / ``set_quote`` (replay, tests) or, when a
``market_data`` source is given, from that source on every ``tick()`` call
(paper trading on live prices). SL/TP and pending orders are checked whenever
a new price arrives.
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from maq_brokers.base import (
    BrokerRejection,
    EventHub,
    MarketData,
    NoPriceError,
    Subscription,
    UnknownSymbolError,
)
from maq_brokers.events import BrokerEvent, OrderCancelled, OrderFilled, PositionClosed
from maq_core.clock import Clock, SystemClock
from maq_core.domain import (
    GRID_EPSILON,
    AccountKind,
    AccountState,
    Bar,
    ClosedTrade,
    CloseReason,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    RejectReason,
    Side,
    SymbolSpec,
    Tick,
    Timeframe,
    WorkingOrder,
)


@dataclass(frozen=True, slots=True)
class CostModel:
    """Trading costs, in the account currency."""

    commission_per_lot: float = 0.0  # charged on every deal (open and close)
    slippage_points: int = 0  # adverse, on market fills, stops and stop-outs
    swap_long_per_lot: float = 0.0  # per rollover; negative means you pay
    swap_short_per_lot: float = 0.0

    def __post_init__(self) -> None:
        for name in ("commission_per_lot", "swap_long_per_lot", "swap_short_per_lot"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number")
        if self.commission_per_lot < 0:
            raise ValueError("commission_per_lot is a cost: give it as a positive number")
        if self.slippage_points < 0:
            raise ValueError("slippage_points must not be negative")

    def swap_per_lot(self, side: Side) -> float:
        return self.swap_long_per_lot if side is Side.BUY else self.swap_short_per_lot


@dataclass
class _OpenPosition:
    position_id: str
    symbol: str
    side: Side
    volume: float
    open_price: float
    open_time: datetime
    sl: float | None
    tp: float | None
    magic: int
    client_order_id: str | None
    swap: float = 0.0
    commission: float = 0.0  # entry commission not yet allocated to a closed part


def _money(value: float) -> float:
    return round(value, 2)


class PaperBroker:
    """A ``Broker`` that matches orders locally. See the module docstring."""

    def __init__(
        self,
        specs: Iterable[SymbolSpec] = (),
        *,
        clock: Clock | None = None,
        balance: float = 10_000.0,
        currency: str = "USD",
        leverage: int = 100,
        costs: CostModel | Mapping[str, CostModel] | None = None,
        stop_out_level: float = 50.0,
        market_data: MarketData | None = None,
        rollover_hour_utc: int = 21,
        triple_swap_weekday: int = 2,
        name: str = "paper",
    ) -> None:
        if not math.isfinite(balance) or balance <= 0:
            raise ValueError("balance must be a positive number")
        if leverage < 1:
            raise ValueError("leverage must be at least 1")
        if not 0 <= rollover_hour_utc <= 23:
            raise ValueError("rollover_hour_utc must be between 0 and 23")
        self._name = name
        self._clock = clock or SystemClock()
        self._specs: dict[str, SymbolSpec] = {spec.symbol: spec for spec in specs}
        self._balance = float(balance)
        self._currency = currency
        self._leverage = leverage
        if isinstance(costs, CostModel) or costs is None:
            self._default_costs = costs or CostModel()
            self._symbol_costs: dict[str, CostModel] = {}
        else:
            self._default_costs = CostModel()
            self._symbol_costs = dict(costs)
        self._stop_out_level = stop_out_level
        self._market = market_data
        self._rollover_hour = rollover_hour_utc
        self._triple_swap_weekday = triple_swap_weekday

        self._ticks: dict[str, Tick] = {}
        self._bars: dict[tuple[str, Timeframe], tuple[list[datetime], list[Bar]]] = {}
        self._positions: dict[str, _OpenPosition] = {}
        self._working: dict[str, WorkingOrder] = {}
        self._results: dict[str, OrderResult] = {}
        self._closed: list[ClosedTrade] = []
        self._ids = itertools.count(1)
        self._hub = EventHub()
        self._last_time = self._clock.now()

    # ------------------------------------------------------------------ setup
    @property
    def name(self) -> str:
        return self._name

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self._hub.close()

    def add_symbol(self, spec: SymbolSpec) -> None:
        self._specs[spec.symbol] = spec

    def events(self) -> Subscription:
        return self._hub.subscribe()

    # ------------------------------------------------------------------ prices
    def feed_tick(self, tick: Tick) -> None:
        """Take a new quote and react to it: pending orders, SL/TP, stop-out."""
        if tick.symbol not in self._specs:
            raise UnknownSymbolError(tick.symbol)
        self._advance_time()
        self._ticks[tick.symbol] = tick
        self._trigger_working_orders(tick)
        self._trigger_stops(tick)
        self._check_stop_out()

    def set_quote(self, symbol: str, bid: float, ask: float) -> Tick:
        """``feed_tick`` with the clock's current time."""
        tick = Tick(symbol=symbol, time=self._clock.now(), bid=bid, ask=ask)
        self.feed_tick(tick)
        return tick

    def set_bars(self, symbol: str, timeframe: Timeframe, bars: Iterable[Bar]) -> None:
        """Replace the bar history; bars must be in ascending time order."""
        ordered = list(bars)
        times = [bar.time for bar in ordered]
        if any(later <= earlier for earlier, later in itertools.pairwise(times)):
            raise ValueError("bars must be in strictly ascending time order")
        self._bars[(symbol, timeframe)] = (times, ordered)

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        spec = self._specs.get(symbol)
        if spec is None and self._market is not None:
            spec = await self._market.symbol_spec(symbol)
            self._specs[symbol] = spec
        if spec is None:
            raise UnknownSymbolError(symbol)
        return spec

    async def tick(self, symbol: str) -> Tick:
        if self._market is not None:
            await self.symbol_spec(symbol)
            tick = await self._market.tick(symbol)
            self.feed_tick(tick)
            return tick
        if symbol not in self._specs:
            raise UnknownSymbolError(symbol)
        tick_ = self._ticks.get(symbol)
        if tick_ is None:
            raise NoPriceError(f"no quote for {symbol}")
        return tick_

    async def bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        """Completed bars only: a bar is visible once its period has ended."""
        if count <= 0:
            raise ValueError("count must be positive")
        if self._market is not None:
            return await self._market.bars(symbol, timeframe, count)
        if symbol not in self._specs:
            raise UnknownSymbolError(symbol)
        times, bars = self._bars.get((symbol, timeframe), ([], []))
        end = bisect.bisect_right(times, self._clock.now() - timeframe.duration)
        return bars[max(0, end - count) : end]

    # ------------------------------------------------------------------ account
    async def account(self) -> AccountState:
        self._advance_time()
        equity = self._equity()
        margin = self._used_margin()
        return AccountState(
            kind=AccountKind.PAPER,
            currency=self._currency,
            balance=_money(self._balance),
            equity=_money(equity),
            margin=_money(margin),
            margin_free=_money(equity - margin),
            leverage=self._leverage,
            server=self._name,
        )

    async def positions(self, symbol: str | None = None) -> list[Position]:
        self._advance_time()
        return [
            self._position_view(pos)
            for pos in self._positions.values()
            if symbol is None or pos.symbol == symbol
        ]

    async def working_orders(self, symbol: str | None = None) -> list[WorkingOrder]:
        self._advance_time()
        return [o for o in self._working.values() if symbol is None or o.symbol == symbol]

    async def closed_trades(self, since: datetime | None = None) -> list[ClosedTrade]:
        self._advance_time()
        return [t for t in self._closed if since is None or t.close_time >= since]

    # ------------------------------------------------------------------ orders
    async def submit(self, order: OrderRequest) -> OrderResult:
        self._advance_time()
        previous = self._results.get(order.client_order_id)
        if previous is not None:
            return previous
        result = self._execute(order)
        self._results[order.client_order_id] = result
        return result

    async def find_order(self, client_order_id: str) -> OrderResult | None:
        self._advance_time()
        return self._results.get(client_order_id)

    async def cancel(self, client_order_id: str) -> OrderResult:
        self._advance_time()
        working = self._working.pop(client_order_id, None)
        if working is None:
            previous = self._results.get(client_order_id)
            if previous is not None and previous.status is OrderStatus.CANCELLED:
                return previous
            raise BrokerRejection(RejectReason.NOT_FOUND, f"no working order {client_order_id!r}")
        result = self._finish_working(working, OrderStatus.CANCELLED)
        return result

    async def close_position(
        self, position_id: str, *, client_order_id: str, volume: float | None = None
    ) -> OrderResult:
        if volume is not None and not (math.isfinite(volume) and volume > 0):
            raise ValueError(f"volume must be a positive number, got {volume!r}")
        self._advance_time()
        previous = self._results.get(client_order_id)
        if previous is not None:
            return previous
        pos = self._positions.get(position_id)
        if pos is None:
            raise BrokerRejection(RejectReason.NOT_FOUND, f"no open position {position_id!r}")

        spec = self._specs[pos.symbol]
        close_side = pos.side.opposite
        lots = pos.volume if volume is None else volume
        remaining = pos.volume - lots
        tick = self._ticks.get(pos.symbol)
        result: OrderResult
        if (
            not spec.is_valid_volume(lots)
            or remaining < -GRID_EPSILON
            or GRID_EPSILON < remaining < spec.volume_min - GRID_EPSILON
        ):
            result = self._rejected(
                client_order_id,
                pos.symbol,
                close_side,
                lots,
                RejectReason.INVALID_VOLUME,
                f"cannot close {lots} of {pos.volume} lots",
            )
        elif tick is None:
            result = self._rejected(
                client_order_id,
                pos.symbol,
                close_side,
                lots,
                RejectReason.MARKET_CLOSED,
                "no quote",
            )
        else:
            price = self._slipped(spec, tick.exit_price(pos.side), close_side)
            exit_commission = self._close(
                pos, lots, price, CloseReason.CLIENT, closing_order_id=client_order_id
            )
            result = OrderResult(
                client_order_id=client_order_id,
                status=OrderStatus.FILLED,
                symbol=pos.symbol,
                side=close_side,
                requested_volume=lots,
                filled_volume=lots,
                avg_fill_price=price,
                time=self._clock.now(),
                position_id=position_id,
                commission=exit_commission,
            )
        self._results[client_order_id] = result
        return result

    async def modify_position(
        self, position_id: str, *, sl: float | None, tp: float | None
    ) -> Position:
        self._advance_time()
        pos = self._positions.get(position_id)
        if pos is None:
            raise BrokerRejection(RejectReason.NOT_FOUND, f"no open position {position_id!r}")
        tick = self._ticks.get(pos.symbol)
        if tick is None:
            raise BrokerRejection(RejectReason.MARKET_CLOSED, f"no quote for {pos.symbol}")
        spec = self._specs[pos.symbol]
        problem = _stops_problem(spec, pos.side, tick.exit_price(pos.side), sl, tp)
        if problem:
            raise BrokerRejection(RejectReason.INVALID_STOPS, problem)
        pos.sl = spec.normalize_price(sl) if sl is not None else None
        pos.tp = spec.normalize_price(tp) if tp is not None else None
        return self._position_view(pos)

    # ------------------------------------------------------------------ matching
    def _execute(self, order: OrderRequest) -> OrderResult:
        def reject(reason: RejectReason, message: str) -> OrderResult:
            return self._rejected(
                order.client_order_id, order.symbol, order.side, order.volume, reason, message
            )

        spec = self._specs.get(order.symbol)
        if spec is None:
            return reject(RejectReason.UNKNOWN_SYMBOL, f"unknown symbol {order.symbol!r}")
        if not spec.trade_allowed:
            return reject(RejectReason.TRADE_DISABLED, f"trading {order.symbol} is disabled")
        if not spec.is_valid_volume(order.volume):
            return reject(
                RejectReason.INVALID_VOLUME,
                f"volume {order.volume} is not a multiple of {spec.volume_step} "
                f"within [{spec.volume_min}, {spec.volume_max}]",
            )
        tick = self._ticks.get(order.symbol)
        if tick is None:
            return reject(RejectReason.MARKET_CLOSED, f"no quote for {order.symbol}")

        if order.type is OrderType.MARKET:
            price = self._slipped(spec, tick.entry_price(order.side), order.side)
            allowed = order.max_slippage_points * spec.point
            if order.price is not None and abs(price - order.price) > allowed + GRID_EPSILON:
                return reject(
                    RejectReason.PRICE_CHANGED,
                    f"price moved to {price} from {order.price} "
                    f"(more than {order.max_slippage_points} points)",
                )
            problem = _stops_problem(
                spec, order.side, tick.exit_price(order.side), order.sl, order.tp
            )
            if problem:
                return reject(RejectReason.INVALID_STOPS, problem)
            return self._open(
                spec,
                order.client_order_id,
                order.side,
                order.volume,
                price,
                sl=order.sl,
                tp=order.tp,
                magic=order.magic,
            )

        assert order.price is not None  # guaranteed by OrderRequest
        problem = _pending_price_problem(spec, order, tick)
        if problem:
            return reject(RejectReason.INVALID_PRICE, problem)
        problem = _stops_problem(spec, order.side, order.price, order.sl, order.tp)
        if problem:
            return reject(RejectReason.INVALID_STOPS, problem)
        now = self._clock.now()
        if order.expires_at is not None and order.expires_at <= now:
            return reject(RejectReason.INVALID_REQUEST, "expires_at is already in the past")
        broker_id = str(next(self._ids))
        self._working[order.client_order_id] = WorkingOrder(
            client_order_id=order.client_order_id,
            broker_order_id=broker_id,
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            volume=order.volume,
            price=spec.normalize_price(order.price),
            sl=order.sl,
            tp=order.tp,
            placed_at=now,
            expires_at=order.expires_at,
            magic=order.magic,
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            status=OrderStatus.ACCEPTED,
            symbol=order.symbol,
            side=order.side,
            requested_volume=order.volume,
            time=now,
            broker_order_id=broker_id,
        )

    def _open(
        self,
        spec: SymbolSpec,
        client_order_id: str,
        side: Side,
        volume: float,
        price: float,
        *,
        sl: float | None,
        tp: float | None,
        magic: int,
        broker_order_id: str | None = None,
    ) -> OrderResult:
        required = spec.margin(volume, price, self._leverage)
        free = self._equity() - self._used_margin()
        if required > free + GRID_EPSILON:
            return self._rejected(
                client_order_id,
                spec.symbol,
                side,
                volume,
                RejectReason.NO_MONEY,
                f"needs {required:.2f} {self._currency} margin, {free:.2f} free",
            )
        now = self._clock.now()
        commission = _money(-self._costs_for(spec.symbol).commission_per_lot * volume)
        self._balance += commission
        position_id = str(next(self._ids))
        self._positions[position_id] = _OpenPosition(
            position_id=position_id,
            symbol=spec.symbol,
            side=side,
            volume=volume,
            open_price=price,
            open_time=now,
            sl=spec.normalize_price(sl) if sl is not None else None,
            tp=spec.normalize_price(tp) if tp is not None else None,
            magic=magic,
            client_order_id=client_order_id,
            commission=commission,
        )
        self._publish(
            OrderFilled(
                ts=now,
                client_order_id=client_order_id,
                position_id=position_id,
                symbol=spec.symbol,
                side=side,
                volume=volume,
                price=price,
                commission=commission,
            )
        )
        return OrderResult(
            client_order_id=client_order_id,
            status=OrderStatus.FILLED,
            symbol=spec.symbol,
            side=side,
            requested_volume=volume,
            filled_volume=volume,
            avg_fill_price=price,
            time=now,
            broker_order_id=broker_order_id or position_id,
            position_id=position_id,
            commission=commission,
        )

    def _close(
        self,
        pos: _OpenPosition,
        volume: float,
        price: float,
        reason: CloseReason,
        *,
        closing_order_id: str | None = None,
    ) -> float:
        """Close ``volume`` lots of ``pos`` at ``price``; returns the exit commission."""
        spec = self._specs[pos.symbol]
        now = self._clock.now()
        fraction = min(volume / pos.volume, 1.0)
        profit = _money(spec.profit(pos.side, volume, pos.open_price, price))
        swap = _money(pos.swap * fraction)
        entry_commission = _money(pos.commission * fraction)
        exit_commission = _money(-self._costs_for(pos.symbol).commission_per_lot * volume)
        self._balance += profit + swap + exit_commission

        pos.volume = round(pos.volume - volume, 10)
        pos.swap -= swap
        pos.commission -= entry_commission
        if pos.volume <= GRID_EPSILON:
            del self._positions[pos.position_id]

        trade = ClosedTrade(
            position_id=pos.position_id,
            symbol=pos.symbol,
            side=pos.side,
            volume=volume,
            open_price=pos.open_price,
            close_price=price,
            open_time=pos.open_time,
            close_time=now,
            profit=profit,
            swap=swap,
            commission=_money(entry_commission + exit_commission),
            reason=reason,
            client_order_id=pos.client_order_id,
        )
        self._closed.append(trade)
        self._publish(
            OrderFilled(
                ts=now,
                client_order_id=closing_order_id,
                position_id=pos.position_id,
                symbol=pos.symbol,
                side=pos.side.opposite,
                volume=volume,
                price=price,
                commission=exit_commission,
                opening=False,
            )
        )
        self._publish(PositionClosed(ts=now, trade=trade))
        return exit_commission

    def _trigger_working_orders(self, tick: Tick) -> None:
        for key, order in list(self._working.items()):
            if order.symbol != tick.symbol:
                continue
            spec = self._specs[order.symbol]
            market = tick.entry_price(order.side)
            if order.type is OrderType.LIMIT:
                # BUY LIMIT: the ask came down to the limit; fill there or better.
                triggered = order.side.sign * (order.price - market) >= -GRID_EPSILON
                price = market
            else:
                # BUY STOP: the ask went up through the stop; fill at the market.
                triggered = order.side.sign * (market - order.price) >= -GRID_EPSILON
                price = self._slipped(spec, market, order.side)
            if not triggered:
                continue
            del self._working[key]
            self._results[key] = self._open(
                spec,
                key,
                order.side,
                order.volume,
                price,
                sl=order.sl,
                tp=order.tp,
                magic=order.magic,
                broker_order_id=order.broker_order_id,
            )

    def _trigger_stops(self, tick: Tick) -> None:
        for pos in list(self._positions.values()):
            if pos.symbol != tick.symbol:
                continue
            spec = self._specs[pos.symbol]
            exit_price = tick.exit_price(pos.side)
            if pos.sl is not None and pos.side.sign * (exit_price - pos.sl) <= GRID_EPSILON:
                price = self._slipped(spec, exit_price, pos.side.opposite)
                self._close(pos, pos.volume, price, CloseReason.STOP_LOSS)
            elif pos.tp is not None and pos.side.sign * (exit_price - pos.tp) >= -GRID_EPSILON:
                self._close(pos, pos.volume, pos.tp, CloseReason.TAKE_PROFIT)

    def _check_stop_out(self) -> None:
        while self._positions:
            margin = self._used_margin()
            if margin <= 0 or self._equity() / margin * 100 >= self._stop_out_level:
                return
            worst = min(self._positions.values(), key=self._floating_profit)
            tick = self._ticks.get(worst.symbol)
            if tick is None:
                return
            spec = self._specs[worst.symbol]
            price = self._slipped(spec, tick.exit_price(worst.side), worst.side.opposite)
            self._close(worst, worst.volume, price, CloseReason.STOP_OUT)

    # ------------------------------------------------------------------ time
    def _advance_time(self) -> None:
        now = self._clock.now()
        for key, order in list(self._working.items()):
            if order.expires_at is not None and order.expires_at <= now:
                del self._working[key]
                self._finish_working(order, OrderStatus.EXPIRED)
        if now > self._last_time:
            self._charge_swaps(self._last_time, now)
            self._last_time = now

    def _charge_swaps(self, start: datetime, end: datetime) -> None:
        """Charge swap for every rollover in (start, end], weekdays only."""
        if not self._positions:
            return
        rollover = datetime.combine(start.date(), time(self._rollover_hour), tzinfo=UTC)
        if rollover <= start:
            rollover += timedelta(days=1)
        while rollover <= end:
            if rollover.weekday() < 5:
                nights = 3 if rollover.weekday() == self._triple_swap_weekday else 1
                for pos in self._positions.values():
                    if pos.open_time < rollover:
                        costs = self._costs_for(pos.symbol)
                        pos.swap += costs.swap_per_lot(pos.side) * pos.volume * nights
            rollover += timedelta(days=1)

    def _finish_working(self, order: WorkingOrder, status: OrderStatus) -> OrderResult:
        result = OrderResult(
            client_order_id=order.client_order_id,
            status=status,
            symbol=order.symbol,
            side=order.side,
            requested_volume=order.volume,
            time=self._clock.now(),
            broker_order_id=order.broker_order_id,
        )
        self._results[order.client_order_id] = result
        self._publish(
            OrderCancelled(
                ts=result.time,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                expired=status is OrderStatus.EXPIRED,
            )
        )
        return result

    # ------------------------------------------------------------------ helpers
    def _costs_for(self, symbol: str) -> CostModel:
        return self._symbol_costs.get(symbol, self._default_costs)

    def _slipped(self, spec: SymbolSpec, price: float, deal_side: Side) -> float:
        slip = self._costs_for(spec.symbol).slippage_points * spec.point
        return spec.normalize_price(price + deal_side.sign * slip)

    def _floating_profit(self, pos: _OpenPosition) -> float:
        tick = self._ticks.get(pos.symbol)
        price = tick.exit_price(pos.side) if tick else pos.open_price
        return self._specs[pos.symbol].profit(pos.side, pos.volume, pos.open_price, price)

    def _equity(self) -> float:
        return self._balance + sum(
            self._floating_profit(pos) + pos.swap for pos in self._positions.values()
        )

    def _used_margin(self) -> float:
        total = 0.0
        for pos in self._positions.values():
            tick = self._ticks.get(pos.symbol)
            price = tick.mid if tick else pos.open_price
            total += self._specs[pos.symbol].margin(pos.volume, price, self._leverage)
        return total

    def _position_view(self, pos: _OpenPosition) -> Position:
        tick = self._ticks.get(pos.symbol)
        return Position(
            position_id=pos.position_id,
            symbol=pos.symbol,
            side=pos.side,
            volume=pos.volume,
            open_price=pos.open_price,
            open_time=pos.open_time,
            sl=pos.sl,
            tp=pos.tp,
            current_price=tick.exit_price(pos.side) if tick else None,
            profit=_money(self._floating_profit(pos)),
            swap=_money(pos.swap),
            commission=_money(pos.commission),
            magic=pos.magic,
            client_order_id=pos.client_order_id,
        )

    def _rejected(
        self,
        client_order_id: str,
        symbol: str,
        side: Side,
        volume: float,
        reason: RejectReason,
        message: str,
    ) -> OrderResult:
        return OrderResult(
            client_order_id=client_order_id,
            status=OrderStatus.REJECTED,
            symbol=symbol,
            side=side,
            requested_volume=volume,
            time=self._clock.now(),
            reject_reason=reason,
            message=message,
        )

    def _publish(self, event: BrokerEvent) -> None:
        self._hub.publish(event.model_copy(update={"broker": self._name}))


def _stops_problem(
    spec: SymbolSpec, side: Side, reference: float, sl: float | None, tp: float | None
) -> str | None:
    """Why SL/TP are not acceptable relative to ``reference`` (the price a position closes at)."""
    minimum = spec.min_stop_distance
    if sl is not None:
        distance = side.sign * (reference - sl)
        if distance <= GRID_EPSILON or distance < minimum - GRID_EPSILON:
            where = "below" if side is Side.BUY else "above"
            return (
                f"stop loss {sl} must be {where} {reference} by at least {spec.stops_level} points"
            )
    if tp is not None:
        distance = side.sign * (tp - reference)
        if distance <= GRID_EPSILON or distance < minimum - GRID_EPSILON:
            where = "above" if side is Side.BUY else "below"
            return f"take profit {tp} must be {where} {reference} by at least {spec.stops_level} points"
    return None


def _pending_price_problem(spec: SymbolSpec, order: OrderRequest, tick: Tick) -> str | None:
    assert order.price is not None
    market = tick.entry_price(order.side)
    if order.type is OrderType.LIMIT:
        distance = order.side.sign * (market - order.price)  # BUY LIMIT sits below the ask
    else:
        distance = order.side.sign * (order.price - market)  # BUY STOP sits above the ask
    if distance <= GRID_EPSILON or distance < spec.min_stop_distance - GRID_EPSILON:
        return (
            f"{order.side} {order.type} at {order.price} is on the wrong side of the market "
            f"({market}) or closer than {spec.stops_level} points"
        )
    return None
