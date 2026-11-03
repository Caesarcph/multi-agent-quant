"""PaperBroker: the broker contract plus what only a simulator has (costs, time, stop-out)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from maq_brokers import (
    BrokerRejection,
    CostModel,
    NoPriceError,
    OrderCancelled,
    PaperBroker,
    PositionClosed,
    UnknownSymbolError,
)
from maq_brokers.testing import ASK, BID, BrokerContract, BrokerHarness
from maq_core.clock import ManualClock
from maq_core.domain import (
    AccountKind,
    Bar,
    CloseReason,
    OrderRequest,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
    SymbolSpec,
    Tick,
    Timeframe,
)

# A Monday, so rollovers in the tests fall on weekdays unless stated otherwise.
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EURUSD = SymbolSpec(
    symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
    currency_base="EUR", currency_profit="USD", currency_margin="EUR",
)  # fmt: skip
GBPUSD = EURUSD.model_copy(update={"symbol": "GBPUSD"})


def m15_bars(end: datetime, count: int, start_price: float = 1.1) -> list[Bar]:
    first = end - timedelta(minutes=15 * count)
    bars = []
    for i in range(count):
        price = start_price + i * 0.0001
        bars.append(
            Bar(
                time=first + timedelta(minutes=15 * i),
                open=price,
                high=price + 0.0005,
                low=price - 0.0005,
                close=price + 0.0002,
            )
        )
    return bars


def make_broker(clock: ManualClock, **kwargs: object) -> PaperBroker:
    broker = PaperBroker([EURUSD, GBPUSD], clock=clock, **kwargs)  # type: ignore[arg-type]
    broker.set_quote("EURUSD", BID, ASK)
    broker.set_bars("EURUSD", Timeframe.M15, m15_bars(clock.now(), 30))
    return broker


@dataclass
class PaperHarness:
    broker: PaperBroker
    clock: ManualClock
    symbol: str = "EURUSD"
    quotes: list[tuple[float, float]] = field(default_factory=list)

    def set_quote(self, bid: float, ask: float) -> None:
        self.broker.set_quote(self.symbol, bid, ask)

    async def settle(self) -> None:
        return None


class TestPaperBrokerContract(BrokerContract):
    @pytest.fixture
    async def harness(self) -> AsyncIterator[BrokerHarness]:
        clock = ManualClock(T0)
        broker = make_broker(clock)
        await broker.connect()
        yield PaperHarness(broker, clock)
        await broker.close()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


def buy(key: str, volume: float = 0.1, **extra: object) -> OrderRequest:
    return OrderRequest.model_validate(
        {"client_order_id": key, "symbol": "EURUSD", "side": "BUY", "volume": volume, **extra}
    )


def sell(key: str, volume: float = 0.1, **extra: object) -> OrderRequest:
    return OrderRequest.model_validate(
        {"client_order_id": key, "symbol": "EURUSD", "side": "SELL", "volume": volume, **extra}
    )


class TestCosts:
    async def test_commission_is_charged_on_both_deals(self, clock: ManualClock) -> None:
        broker = make_broker(clock, costs=CostModel(commission_per_lot=7.0))
        opened = await broker.submit(buy("c1", 1.0))
        assert opened.commission == pytest.approx(-7.0)
        [position] = await broker.positions()
        assert position.commission == pytest.approx(-7.0)
        assert (await broker.account()).balance == pytest.approx(10_000 - 7.0)

        assert opened.position_id is not None
        closed = await broker.close_position(opened.position_id, client_order_id="c1-close")
        assert closed.commission == pytest.approx(-7.0)
        [trade] = await broker.closed_trades()
        assert trade.commission == pytest.approx(-14.0)
        # Spread (10 points on a lot = 10.0) plus 14.0 commission.
        assert trade.net_profit == pytest.approx(-24.0)
        assert (await broker.account()).balance == pytest.approx(10_000 - 24.0)

    async def test_slippage_is_adverse_on_market_fills(self, clock: ManualClock) -> None:
        broker = make_broker(clock, costs=CostModel(slippage_points=3))
        long = await broker.submit(buy("s1"))
        short = await broker.submit(sell("s2"))
        assert long.avg_fill_price == pytest.approx(ASK + 0.00003)
        assert short.avg_fill_price == pytest.approx(BID - 0.00003)

    async def test_costs_per_symbol(self, clock: ManualClock) -> None:
        broker = make_broker(clock, costs={"EURUSD": CostModel(commission_per_lot=5.0)})
        broker.set_quote("GBPUSD", 1.3, 1.3001)
        eur = await broker.submit(buy("e1", 1.0))
        gbp = await broker.submit(
            OrderRequest(client_order_id="g1", symbol="GBPUSD", side=Side.BUY, volume=1.0)
        )
        assert eur.commission == pytest.approx(-5.0)
        assert gbp.commission == pytest.approx(0.0)

    def test_cost_model_validation(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            CostModel(commission_per_lot=-1)
        with pytest.raises(ValueError, match="slippage"):
            CostModel(slippage_points=-1)
        with pytest.raises(ValueError, match="finite"):
            CostModel(swap_long_per_lot=float("nan"))


class TestSwap:
    async def test_swap_at_rollover_triple_on_wednesday(self, clock: ManualClock) -> None:
        broker = make_broker(clock, costs=CostModel(swap_long_per_lot=-2.0, swap_short_per_lot=1.0))
        await broker.submit(buy("w1", 1.0))
        await broker.submit(sell("w2", 1.0))

        clock.set(datetime(2026, 11, 2, 22, tzinfo=UTC))  # past Monday's 21:00 rollover
        long, short = await broker.positions()
        assert long.swap == pytest.approx(-2.0)
        assert short.swap == pytest.approx(1.0)

        clock.set(datetime(2026, 11, 4, 22, tzinfo=UTC))  # Tuesday x1 + Wednesday x3
        long, short = await broker.positions()
        assert long.swap == pytest.approx(-2.0 * 5)
        assert short.swap == pytest.approx(1.0 * 5)

    async def test_no_swap_on_weekends_or_before_open(self, clock: ManualClock) -> None:
        clock.set(datetime(2026, 11, 7, 12, tzinfo=UTC))  # Saturday
        broker = make_broker(clock, costs=CostModel(swap_long_per_lot=-2.0))
        await broker.submit(buy("w3", 1.0))
        clock.set(datetime(2026, 11, 8, 22, tzinfo=UTC))  # Sunday night
        [position] = await broker.positions()
        assert position.swap == pytest.approx(0.0)

    async def test_swap_is_realised_on_close(self, clock: ManualClock) -> None:
        broker = make_broker(clock, costs=CostModel(swap_long_per_lot=-2.0))
        opened = await broker.submit(buy("w4", 1.0))
        clock.advance(days=1)
        assert opened.position_id is not None
        await broker.close_position(opened.position_id, client_order_id="w4-close")
        [trade] = await broker.closed_trades()
        assert trade.swap == pytest.approx(-2.0)
        assert (await broker.account()).balance == pytest.approx(10_000 - 10.0 - 2.0)


class TestTime:
    async def test_pending_order_expires(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        events = broker.events()
        expiry = T0 + timedelta(hours=1)
        result = await broker.submit(
            buy("x1", type=OrderType.LIMIT, price=1.099, expires_at=expiry)
        )
        assert result.status is OrderStatus.ACCEPTED
        clock.set(expiry)
        assert await broker.working_orders() == []
        expired = await broker.find_order("x1")
        assert expired is not None
        assert expired.status is OrderStatus.EXPIRED
        [event] = [e for e in events.drain() if isinstance(e, OrderCancelled)]
        assert event.expired

    async def test_expiry_in_the_past_is_rejected(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        result = await broker.submit(
            buy("x2", type=OrderType.LIMIT, price=1.099, expires_at=T0 - timedelta(seconds=1))
        )
        assert result.reject_reason is RejectReason.INVALID_REQUEST

    async def test_bars_never_show_the_future(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        future = m15_bars(T0 + timedelta(hours=2), 8, start_price=1.2)
        broker.set_bars("EURUSD", Timeframe.M15, m15_bars(T0, 30) + future)
        bars = await broker.bars("EURUSD", Timeframe.M15, 100)
        assert len(bars) == 30
        assert all(bar.time + timedelta(minutes=15) <= T0 for bar in bars)
        clock.advance(minutes=15)  # one more bar has closed
        assert len(await broker.bars("EURUSD", Timeframe.M15, 100)) == 31

    async def test_bars_must_be_ordered(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        bars = m15_bars(T0, 3)
        with pytest.raises(ValueError, match="ascending"):
            broker.set_bars("EURUSD", Timeframe.M15, list(reversed(bars)))
        with pytest.raises(ValueError, match="positive"):
            await broker.bars("EURUSD", Timeframe.M15, 0)


class TestRisky:
    async def test_stop_loss_gap_fills_at_the_market(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        await broker.submit(buy("g1", 1.0, sl=1.098))
        broker.set_quote("EURUSD", 1.0950, 1.0951)  # weekend gap straight through the stop
        [trade] = await broker.closed_trades()
        assert trade.reason is CloseReason.STOP_LOSS
        assert trade.close_price == pytest.approx(1.0950)
        assert trade.profit == pytest.approx((1.0950 - ASK) * 100_000)

    async def test_stop_out_closes_the_worst_position(self, clock: ManualClock) -> None:
        broker = make_broker(clock, balance=1_000.0, stop_out_level=50.0)
        events = broker.events()
        # 0.5 lot needs ~550 margin; equity 1000 -> margin level ~180%.
        await broker.submit(buy("so1", 0.5))
        # A 1,500-point drop costs 750: equity ~250, margin level < 50% -> stop-out.
        broker.set_quote("EURUSD", 1.0850, 1.0851)
        assert await broker.positions() == []
        [trade] = await broker.closed_trades()
        assert trade.reason is CloseReason.STOP_OUT
        assert [e.trade.reason for e in events.drain() if isinstance(e, PositionClosed)] == [
            CloseReason.STOP_OUT
        ]

    async def test_price_moved_beyond_allowed_slippage(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        stale = buy("pc1", price=1.0990, max_slippage_points=20)  # decided at 1.0990
        result = await broker.submit(stale)
        assert result.reject_reason is RejectReason.PRICE_CHANGED
        fresh = buy("pc2", price=ASK - 0.00005, max_slippage_points=20)
        assert (await broker.submit(fresh)).status is OrderStatus.FILLED

    async def test_stops_level_is_enforced(self, clock: ManualClock) -> None:
        spec = EURUSD.model_copy(update={"stops_level": 50})  # 5 pips
        broker = PaperBroker([spec], clock=clock)
        broker.set_quote("EURUSD", BID, ASK)
        too_close = await broker.submit(buy("sl1", sl=BID - 0.0003))
        assert too_close.reject_reason is RejectReason.INVALID_STOPS
        ok = await broker.submit(buy("sl2", sl=BID - 0.0006))
        assert ok.status is OrderStatus.FILLED

    async def test_trading_disabled(self, clock: ManualClock) -> None:
        broker = PaperBroker([EURUSD.model_copy(update={"trade_allowed": False})], clock=clock)
        broker.set_quote("EURUSD", BID, ASK)
        result = await broker.submit(buy("td1"))
        assert result.reject_reason is RejectReason.TRADE_DISABLED

    async def test_no_quote_yet(self, clock: ManualClock) -> None:
        broker = PaperBroker([EURUSD], clock=clock)
        with pytest.raises(NoPriceError):
            await broker.tick("EURUSD")
        result = await broker.submit(buy("nq1"))
        assert result.reject_reason is RejectReason.MARKET_CLOSED
        with pytest.raises(UnknownSymbolError):
            broker.set_quote("XAUUSD", 1, 2)
        with pytest.raises(UnknownSymbolError):
            await broker.tick("XAUUSD")


class TestManagement:
    async def test_pending_order_with_stops_keeps_them(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        await broker.submit(buy("pl1", type=OrderType.LIMIT, price=1.0990, sl=1.0950, tp=1.1050))
        broker.set_quote("EURUSD", 1.0985, 1.0987)
        [position] = await broker.positions()
        assert (position.sl, position.tp) == pytest.approx((1.0950, 1.1050))
        assert position.open_price == pytest.approx(1.0987)  # better than the limit

    async def test_modify_without_a_quote(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        opened = await broker.submit(buy("m1"))
        assert opened.position_id is not None
        broker._ticks.clear()  # simulate a lost feed
        with pytest.raises(BrokerRejection) as error:
            await broker.modify_position(opened.position_id, sl=1.09, tp=None)
        assert error.value.reason is RejectReason.MARKET_CLOSED

    async def test_close_with_bad_volume_argument(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        opened = await broker.submit(buy("cv1", 0.1))
        assert opened.position_id is not None
        with pytest.raises(ValueError, match="positive"):
            await broker.close_position(opened.position_id, client_order_id="cv", volume=0)
        leftover = await broker.close_position(
            opened.position_id, client_order_id="cv2", volume=0.095
        )
        assert leftover.reject_reason is RejectReason.INVALID_VOLUME

    async def test_events_are_tagged_with_the_broker_name(self, clock: ManualClock) -> None:
        broker = make_broker(clock, name="paper-eu")
        events = broker.events()
        await broker.submit(buy("ev1"))
        [event] = events.drain()
        assert event.broker == "paper-eu"
        assert event.ts == T0

    async def test_account_kind_and_validation(self, clock: ManualClock) -> None:
        broker = make_broker(clock)
        account = await broker.account()
        assert account.kind is AccountKind.PAPER
        assert account.margin_level is None
        with pytest.raises(ValueError, match="balance"):
            PaperBroker(balance=0)
        with pytest.raises(ValueError, match="leverage"):
            PaperBroker(leverage=0)
        with pytest.raises(ValueError, match="rollover"):
            PaperBroker(rollover_hour_utc=24)


class FakeFeed:
    """A MarketData source standing in for a live terminal."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.bid, self.ask = BID, ASK
        self.calls: list[str] = []

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        self.calls.append(f"spec:{symbol}")
        if symbol != "EURUSD":
            raise UnknownSymbolError(symbol)
        return EURUSD

    async def tick(self, symbol: str) -> Tick:
        self.calls.append(f"tick:{symbol}")
        return Tick(symbol=symbol, time=self.clock.now(), bid=self.bid, ask=self.ask)

    async def bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        self.calls.append(f"bars:{symbol}")
        return m15_bars(self.clock.now(), count)


class TestLivePrices:
    async def test_paper_trading_on_a_live_feed(self, clock: ManualClock) -> None:
        feed = FakeFeed(clock)
        broker = PaperBroker(clock=clock, market_data=feed)
        tick = await broker.tick("EURUSD")
        assert tick.ask == pytest.approx(ASK)
        assert feed.calls[:2] == ["spec:EURUSD", "tick:EURUSD"]
        assert len(await broker.bars("EURUSD", Timeframe.M15, 5)) == 5

        await broker.submit(buy("lv1", sl=1.0980))
        feed.bid, feed.ask = 1.0970, 1.0971
        await broker.tick("EURUSD")  # polling the feed is what triggers the stop
        [trade] = await broker.closed_trades()
        assert trade.reason is CloseReason.STOP_LOSS

        with pytest.raises(UnknownSymbolError):
            await broker.symbol_spec("XAUUSD")
