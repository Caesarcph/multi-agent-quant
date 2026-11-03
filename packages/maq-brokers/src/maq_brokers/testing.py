"""Contract tests that every ``Broker`` implementation must pass.

Subclass ``BrokerContract`` in your broker's test module and provide a
``harness`` fixture::

    class TestMyBroker(BrokerContract):
        @pytest.fixture
        async def harness(self) -> AsyncIterator[BrokerHarness]:
            broker = MyBroker(...)
            await broker.connect()
            yield MyHarness(broker)
            await broker.close()

The harness must start from this state:

* ``harness.symbol`` is a 5-digit FX pair: point 0.00001, tick value 1.0 per lot
  in the account currency, contract size 100,000, volume grid 0.01 / 0.01 / 100,
  ``stops_level`` 0;
* the quote is bid 1.10000 / ask 1.10010;
* the account holds 10,000 in its currency at leverage 1:100, with no positions
  or working orders, and trades without commission or slippage;
* at least 20 completed M15 bars exist for the symbol.

``set_quote`` moves the market; ``settle`` gives the broker a chance to react
(SL/TP, pending orders, event polling) before the test looks again.

The tests need ``pytest-asyncio`` (``asyncio_mode = "auto"``).
"""

from __future__ import annotations

from typing import Any, Protocol

import pytest

from maq_brokers.base import Broker, BrokerRejection, UnknownSymbolError
from maq_brokers.events import OrderCancelled, OrderFilled, PositionClosed
from maq_core.domain import (
    CloseReason,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
    Timeframe,
)

BID = 1.10000
ASK = 1.10010
START_BALANCE = 10_000.0


class BrokerHarness(Protocol):
    @property
    def broker(self) -> Broker: ...

    @property
    def symbol(self) -> str: ...

    def set_quote(self, bid: float, ask: float) -> None: ...

    async def settle(self) -> None: ...


def order(
    harness: BrokerHarness, key: str, side: Side, volume: float, **extra: Any
) -> OrderRequest:
    data: dict[str, Any] = {
        "client_order_id": key,
        "symbol": harness.symbol,
        "side": side,
        "volume": volume,
    }
    data.update(extra)
    return OrderRequest.model_validate(data)


async def move(harness: BrokerHarness, bid: float, ask: float) -> None:
    harness.set_quote(bid, ask)
    await harness.settle()


async def open_position(
    harness: BrokerHarness, key: str, side: Side, volume: float, **extra: Any
) -> OrderResult:
    result = await harness.broker.submit(order(harness, key, side, volume, **extra))
    assert result.status is OrderStatus.FILLED, result
    assert result.position_id is not None
    return result


class BrokerContract:
    """The behaviour every broker shares. Subclasses provide the ``harness`` fixture."""

    @pytest.fixture
    def harness(self) -> BrokerHarness:
        raise NotImplementedError("a BrokerContract subclass must provide a harness fixture")

    # ------------------------------------------------------------------ account and data
    async def test_starting_account(self, harness: BrokerHarness) -> None:
        account = await harness.broker.account()
        assert account.currency
        assert account.balance == pytest.approx(START_BALANCE)
        assert account.equity == pytest.approx(START_BALANCE)
        assert account.margin == pytest.approx(0.0)
        assert account.leverage == 100
        assert await harness.broker.positions() == []
        assert await harness.broker.working_orders() == []
        assert await harness.broker.closed_trades() == []

    async def test_symbol_spec(self, harness: BrokerHarness) -> None:
        spec = await harness.broker.symbol_spec(harness.symbol)
        assert spec.symbol == harness.symbol
        assert spec.digits == 5
        assert spec.point == pytest.approx(0.00001)
        assert spec.pip_value(1.0) == pytest.approx(10.0)
        assert (spec.volume_min, spec.volume_step) == pytest.approx((0.01, 0.01))
        with pytest.raises(UnknownSymbolError):
            await harness.broker.symbol_spec("NOSUCHSYMBOL")

    async def test_tick_follows_the_market(self, harness: BrokerHarness) -> None:
        tick = await harness.broker.tick(harness.symbol)
        assert tick.symbol == harness.symbol
        assert (tick.bid, tick.ask) == pytest.approx((BID, ASK))
        await move(harness, 1.10020, 1.10030)
        tick = await harness.broker.tick(harness.symbol)
        assert (tick.bid, tick.ask) == pytest.approx((1.10020, 1.10030))

    async def test_bars_are_completed_and_ordered(self, harness: BrokerHarness) -> None:
        bars = await harness.broker.bars(harness.symbol, Timeframe.M15, 10)
        assert len(bars) == 10
        times = [bar.time for bar in bars]
        assert times == sorted(times)
        assert len(set(times)) == len(times)
        latest = await harness.broker.bars(harness.symbol, Timeframe.M15, 1)
        assert [bar.time for bar in latest] == times[-1:]

    # ------------------------------------------------------------------ market orders
    async def test_market_buy_opens_a_position(self, harness: BrokerHarness) -> None:
        events = harness.broker.events()
        result = await open_position(harness, "ct-buy", Side.BUY, 0.1, sl=1.095, tp=1.11)
        assert result.filled_volume == pytest.approx(0.1)
        assert result.avg_fill_price == pytest.approx(ASK)

        [position] = await harness.broker.positions()
        assert position.position_id == result.position_id
        assert (position.symbol, position.side) == (harness.symbol, Side.BUY)
        assert position.volume == pytest.approx(0.1)
        assert position.open_price == pytest.approx(ASK)
        assert (position.sl, position.tp) == pytest.approx((1.095, 1.11))
        assert position.client_order_id == "ct-buy"

        account = await harness.broker.account()
        assert account.margin > 0
        # Marked at the bid: down by the spread, one point on 0.1 lot = 1.0
        assert account.equity == pytest.approx(START_BALANCE - 1.0, abs=0.01)

        await harness.settle()
        fills = [e for e in events.drain() if isinstance(e, OrderFilled)]
        assert [(f.client_order_id, f.opening) for f in fills] == [("ct-buy", True)]
        events.close()

    async def test_market_sell_fills_at_the_bid(self, harness: BrokerHarness) -> None:
        result = await open_position(harness, "ct-sell", Side.SELL, 0.2)
        assert result.avg_fill_price == pytest.approx(BID)
        [position] = await harness.broker.positions()
        assert position.side is Side.SELL
        assert position.volume == pytest.approx(0.2)

    async def test_submit_is_idempotent(self, harness: BrokerHarness) -> None:
        request = order(harness, "ct-once", Side.BUY, 0.1)
        first = await harness.broker.submit(request)
        second = await harness.broker.submit(request)
        bigger = await harness.broker.submit(request.model_copy(update={"volume": 0.5}))
        assert first.status is OrderStatus.FILLED
        assert second == first
        assert bigger == first
        assert len(await harness.broker.positions()) == 1

    async def test_find_order(self, harness: BrokerHarness) -> None:
        assert await harness.broker.find_order("ct-never-sent") is None
        result = await open_position(harness, "ct-find", Side.BUY, 0.1)
        found = await harness.broker.find_order("ct-find")
        assert found is not None
        assert found.status is OrderStatus.FILLED
        assert found.position_id == result.position_id

    # ------------------------------------------------------------------ rejections
    async def test_rejects_volume_off_the_grid(self, harness: BrokerHarness) -> None:
        result = await harness.broker.submit(order(harness, "ct-vol", Side.BUY, 0.015))
        assert result.status is OrderStatus.REJECTED
        assert result.reject_reason is RejectReason.INVALID_VOLUME
        assert await harness.broker.positions() == []

    async def test_rejects_unknown_symbol(self, harness: BrokerHarness) -> None:
        request = order(harness, "ct-sym", Side.BUY, 0.1).model_copy(
            update={"symbol": "NOSUCHSYMBOL"}
        )
        result = await harness.broker.submit(request)
        assert result.reject_reason is RejectReason.UNKNOWN_SYMBOL

    async def test_rejects_stop_loss_on_the_wrong_side(self, harness: BrokerHarness) -> None:
        # A long closes at the bid (1.10000); a stop above it is invalid.
        result = await harness.broker.submit(order(harness, "ct-sl", Side.BUY, 0.1, sl=1.10005))
        assert result.reject_reason is RejectReason.INVALID_STOPS
        assert await harness.broker.positions() == []

    async def test_rejects_orders_beyond_free_margin(self, harness: BrokerHarness) -> None:
        # 50 lots of a 1.1 quote at 1:100 needs about 55,000 of margin.
        result = await harness.broker.submit(order(harness, "ct-margin", Side.BUY, 50))
        assert result.reject_reason is RejectReason.NO_MONEY
        assert await harness.broker.positions() == []

    async def test_rejections_are_remembered(self, harness: BrokerHarness) -> None:
        request = order(harness, "ct-rej", Side.BUY, 0.015)
        first = await harness.broker.submit(request)
        assert await harness.broker.find_order("ct-rej") == first
        assert await harness.broker.submit(request) == first

    # ------------------------------------------------------------------ closing
    async def test_close_position(self, harness: BrokerHarness) -> None:
        opened = await open_position(harness, "ct-open", Side.BUY, 0.1)
        assert opened.position_id is not None
        await move(harness, 1.10200, 1.10210)

        result = await harness.broker.close_position(opened.position_id, client_order_id="ct-close")
        assert result.status is OrderStatus.FILLED
        assert result.side is Side.SELL
        assert result.avg_fill_price == pytest.approx(1.10200)
        assert result.filled_volume == pytest.approx(0.1)
        assert await harness.broker.positions() == []

        [trade] = await harness.broker.closed_trades()
        assert trade.position_id == opened.position_id
        assert trade.reason is CloseReason.CLIENT
        assert trade.profit == pytest.approx(19.0, abs=0.01)  # 19 points on 0.1 lot
        account = await harness.broker.account()
        assert account.balance == pytest.approx(START_BALANCE + 19.0, abs=0.01)
        assert account.equity == pytest.approx(account.balance)

        again = await harness.broker.close_position(opened.position_id, client_order_id="ct-close")
        assert again == result

    async def test_partial_close(self, harness: BrokerHarness) -> None:
        opened = await open_position(harness, "ct-big", Side.SELL, 0.3)
        assert opened.position_id is not None
        result = await harness.broker.close_position(
            opened.position_id, client_order_id="ct-part", volume=0.1
        )
        assert result.status is OrderStatus.FILLED
        [position] = await harness.broker.positions()
        assert position.volume == pytest.approx(0.2)
        [trade] = await harness.broker.closed_trades()
        assert trade.volume == pytest.approx(0.1)

    async def test_cannot_close_more_than_is_open(self, harness: BrokerHarness) -> None:
        opened = await open_position(harness, "ct-small", Side.BUY, 0.1)
        assert opened.position_id is not None
        result = await harness.broker.close_position(
            opened.position_id, client_order_id="ct-toomuch", volume=0.2
        )
        assert result.reject_reason is RejectReason.INVALID_VOLUME
        assert len(await harness.broker.positions()) == 1

    async def test_close_unknown_position(self, harness: BrokerHarness) -> None:
        with pytest.raises(BrokerRejection) as error:
            await harness.broker.close_position("987654321", client_order_id="ct-ghost")
        assert error.value.reason is RejectReason.NOT_FOUND

    # ------------------------------------------------------------------ stops
    async def test_modify_position(self, harness: BrokerHarness) -> None:
        opened = await open_position(harness, "ct-mod", Side.BUY, 0.1)
        assert opened.position_id is not None
        position = await harness.broker.modify_position(opened.position_id, sl=1.095, tp=1.11)
        assert (position.sl, position.tp) == pytest.approx((1.095, 1.11))
        [listed] = await harness.broker.positions()
        assert (listed.sl, listed.tp) == pytest.approx((1.095, 1.11))

        with pytest.raises(BrokerRejection) as error:
            await harness.broker.modify_position(opened.position_id, sl=1.105, tp=None)
        assert error.value.reason is RejectReason.INVALID_STOPS

        cleared = await harness.broker.modify_position(opened.position_id, sl=None, tp=None)
        assert (cleared.sl, cleared.tp) == (None, None)

        with pytest.raises(BrokerRejection) as error:
            await harness.broker.modify_position("987654321", sl=None, tp=None)
        assert error.value.reason is RejectReason.NOT_FOUND

    async def test_stop_loss_closes_a_long(self, harness: BrokerHarness) -> None:
        events = harness.broker.events()
        await open_position(harness, "ct-stop", Side.BUY, 0.1, sl=1.09800)
        await move(harness, 1.09750, 1.09760)

        assert await harness.broker.positions() == []
        [trade] = await harness.broker.closed_trades()
        assert trade.reason is CloseReason.STOP_LOSS
        assert trade.close_price <= 1.09800 + 1e-9
        assert trade.profit < 0
        closed = [e for e in events.drain() if isinstance(e, PositionClosed)]
        assert [e.trade.reason for e in closed] == [CloseReason.STOP_LOSS]
        events.close()

    async def test_take_profit_closes_a_short(self, harness: BrokerHarness) -> None:
        await open_position(harness, "ct-target", Side.SELL, 0.1, tp=1.09800)
        await move(harness, 1.09700, 1.09710)

        assert await harness.broker.positions() == []
        [trade] = await harness.broker.closed_trades()
        assert trade.reason is CloseReason.TAKE_PROFIT
        assert trade.close_price <= 1.09800 + 1e-9
        assert trade.profit > 0

    # ------------------------------------------------------------------ pending orders
    async def test_buy_limit_waits_then_fills(self, harness: BrokerHarness) -> None:
        request = order(harness, "ct-limit", Side.BUY, 0.1, type=OrderType.LIMIT, price=1.09900)
        accepted = await harness.broker.submit(request)
        assert accepted.status is OrderStatus.ACCEPTED
        [working] = await harness.broker.working_orders()
        assert working.client_order_id == "ct-limit"
        assert working.price == pytest.approx(1.09900)
        assert await harness.broker.positions() == []

        await move(harness, 1.09880, 1.09890)
        filled = await harness.broker.find_order("ct-limit")
        assert filled is not None
        assert filled.status is OrderStatus.FILLED
        assert filled.avg_fill_price is not None
        assert filled.avg_fill_price <= 1.09900 + 1e-9
        assert await harness.broker.working_orders() == []
        assert len(await harness.broker.positions()) == 1

    async def test_sell_stop_triggers_below_the_market(self, harness: BrokerHarness) -> None:
        request = order(harness, "ct-sstop", Side.SELL, 0.1, type=OrderType.STOP, price=1.09900)
        assert (await harness.broker.submit(request)).status is OrderStatus.ACCEPTED
        await move(harness, 1.09890, 1.09900)
        filled = await harness.broker.find_order("ct-sstop")
        assert filled is not None
        assert filled.status is OrderStatus.FILLED
        [position] = await harness.broker.positions()
        assert position.side is Side.SELL

    async def test_pending_price_on_the_wrong_side(self, harness: BrokerHarness) -> None:
        # A BUY LIMIT above the ask would fill at once: brokers refuse it.
        request = order(harness, "ct-badlimit", Side.BUY, 0.1, type=OrderType.LIMIT, price=1.101)
        result = await harness.broker.submit(request)
        assert result.reject_reason is RejectReason.INVALID_PRICE

    async def test_cancel_working_order(self, harness: BrokerHarness) -> None:
        events = harness.broker.events()
        request = order(harness, "ct-cancel", Side.BUY, 0.1, type=OrderType.LIMIT, price=1.09)
        await harness.broker.submit(request)

        cancelled = await harness.broker.cancel("ct-cancel")
        assert cancelled.status is OrderStatus.CANCELLED
        assert await harness.broker.working_orders() == []
        found = await harness.broker.find_order("ct-cancel")
        assert found is not None
        assert found.status is OrderStatus.CANCELLED
        assert await harness.broker.cancel("ct-cancel") == cancelled

        with pytest.raises(BrokerRejection) as error:
            await harness.broker.cancel("ct-nothing")
        assert error.value.reason is RejectReason.NOT_FOUND

        await harness.settle()
        assert [e.client_order_id for e in events.drain() if isinstance(e, OrderCancelled)] == [
            "ct-cancel"
        ]
        events.close()
