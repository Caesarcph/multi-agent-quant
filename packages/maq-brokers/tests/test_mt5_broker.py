"""Mt5Broker against a simulated trade server: the contract plus MT5-specific behaviour."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maq_brokers import BrokerConnectionError, OrderFilled, PositionClosed
from maq_brokers.mt5 import Mt5Broker, comment_matches
from maq_brokers.testing import ASK, BID, BrokerContract, BrokerHarness
from maq_core.clock import ManualClock
from maq_core.domain import (
    AccountKind,
    CloseReason,
    OrderRequest,
    OrderStatus,
    RejectReason,
    Side,
    Timeframe,
)
from tests.fakes import mt5 as c
from tests.fakes.mt5_server import SimulatedTerminal

T0 = datetime(2026, 11, 3, 12, tzinfo=UTC)


def make_terminal() -> SimulatedTerminal:
    terminal = SimulatedTerminal(now=T0)
    terminal.add_symbol("EURUSD", bid=BID, ask=ASK)
    terminal.set_rates("EURUSD", [1.1 + i * 0.0001 for i in range(30)])
    return terminal


@dataclass
class Mt5Harness:
    broker: Mt5Broker
    terminal: SimulatedTerminal
    symbol: str = "EURUSD"

    def set_quote(self, bid: float, ask: float) -> None:
        self.terminal.set_quote(self.symbol, bid, ask)

    async def settle(self) -> None:
        await self.broker.poll()


async def connected(terminal: SimulatedTerminal, **kwargs: Any) -> Mt5Broker:
    broker = Mt5Broker(terminal, clock=ManualClock(T0), **kwargs)
    await broker.connect()
    return broker


class TestMt5BrokerContract(BrokerContract):
    @pytest.fixture
    async def harness(self) -> AsyncIterator[BrokerHarness]:
        terminal = make_terminal()
        broker = await connected(terminal)
        yield Mt5Harness(broker, terminal)
        await broker.close()


@pytest.fixture
async def setup() -> AsyncIterator[tuple[Mt5Broker, SimulatedTerminal]]:
    terminal = make_terminal()
    broker = await connected(terminal)
    yield broker, terminal
    await broker.close()


def buy(key: str, volume: float = 0.1, **extra: Any) -> OrderRequest:
    return OrderRequest.model_validate(
        {"client_order_id": key, "symbol": "EURUSD", "side": "BUY", "volume": volume} | extra
    )


class TestRequests:
    async def test_order_carries_the_key_and_a_valid_filling_type(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        await broker.submit(buy("maq-req-1", magic=77, max_slippage_points=15))
        [request] = terminal.sent_requests
        assert request["comment"] == "maq-req-1"
        assert request["magic"] == 77
        assert request["deviation"] == 15
        assert request["type"] == c.ORDER_TYPE_BUY
        assert request["type_filling"] == c.ORDER_FILLING_IOC  # bitmask 3 -> IOC first

    async def test_unsupported_filling_falls_back(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        terminal.order_send_results.append(terminal.result(c.TRADE_RETCODE_INVALID_FILL))
        result = await broker.submit(buy("maq-fill"))
        assert result.status is OrderStatus.FILLED
        assert [r["type_filling"] for r in terminal.sent_requests] == [
            c.ORDER_FILLING_IOC,
            c.ORDER_FILLING_FOK,
        ]

    async def test_order_check_failure_is_a_rejection(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        terminal.order_check_override = {"retcode": c.TRADE_RETCODE_NO_MONEY, "comment": "No money"}
        result = await broker.submit(buy("maq-check"))
        assert result.reject_reason is RejectReason.NO_MONEY
        assert result.broker_code == "10019"
        assert terminal.sent_requests == []  # never sent

    @pytest.mark.parametrize(
        ("retcode", "reason"),
        [
            (c.TRADE_RETCODE_REQUOTE, RejectReason.PRICE_CHANGED),
            (c.TRADE_RETCODE_MARKET_CLOSED, RejectReason.MARKET_CLOSED),
            (c.TRADE_RETCODE_TRADE_DISABLED, RejectReason.TRADE_DISABLED),
            (10099, RejectReason.OTHER),
        ],
    )
    async def test_retcodes_map_to_reasons(
        self, setup: tuple[Mt5Broker, SimulatedTerminal], retcode: int, reason: RejectReason
    ) -> None:
        broker, terminal = setup
        terminal.order_send_results.append(terminal.result(retcode, comment="nope"))
        result = await broker.submit(buy(f"maq-rc-{retcode}"))
        assert result.reject_reason is reason
        assert result.broker_code == str(retcode)

    @pytest.mark.parametrize("answer", [None, "connection"])
    async def test_lost_answer_is_an_unknown_outcome(
        self, setup: tuple[Mt5Broker, SimulatedTerminal], answer: str | None
    ) -> None:
        broker, terminal = setup
        terminal.order_send_results.append(
            None if answer is None else terminal.result(c.TRADE_RETCODE_CONNECTION)
        )
        with pytest.raises(BrokerConnectionError):
            await broker.submit(buy("maq-lost"))
        assert await broker.find_order("maq-lost") is None  # it never reached the server

    async def test_deviation_uses_the_decision_price(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, _ = setup
        stale = await broker.submit(buy("maq-stale", price=1.0990, max_slippage_points=20))
        assert stale.reject_reason is RejectReason.PRICE_CHANGED


class TestIdempotency:
    async def test_order_sent_before_a_restart_is_found_by_its_comment(self) -> None:
        terminal = make_terminal()
        first = await connected(terminal)
        sent = await first.submit(buy("maq-restart"))
        await first.close()

        second = await connected(terminal)  # fresh process: empty cache
        found = await second.find_order("maq-restart")
        assert found is not None
        assert found.status is OrderStatus.FILLED
        assert found.position_id == sent.position_id
        assert found.avg_fill_price == pytest.approx(ASK)
        again = await second.submit(buy("maq-restart"))
        assert again.status is OrderStatus.FILLED
        assert len(terminal.positions) == 1  # not sent twice
        await second.close()

    def test_shortened_comments_still_match(self) -> None:
        key = "maq-0123456789abcdef0123"
        assert comment_matches(key, key)
        assert comment_matches(key[:16], key)
        assert not comment_matches(key[:8], key)  # too short to be sure
        assert not comment_matches("maq-other", key)


class TestServerSide:
    async def test_restart_does_not_replay_old_deals(self) -> None:
        terminal = make_terminal()
        first = await connected(terminal)
        await first.submit(buy("maq-old"))
        await first.close()
        second = await connected(terminal)
        assert await second.poll() == []
        await second.close()

    async def test_manual_close_in_the_terminal_is_reported(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        events = broker.events()
        result = await broker.submit(buy("maq-manual"))
        assert result.position_id is not None
        terminal._exit(terminal.positions[0], 0.1, BID, c.DEAL_REASON_CLIENT, "")
        await broker.poll()
        closes = [e for e in events.drain() if isinstance(e, PositionClosed)]
        assert [e.trade.reason for e in closes] == [CloseReason.OTHER]
        assert closes[0].trade.client_order_id == "maq-manual"

    async def test_server_time_offset(self) -> None:
        terminal = make_terminal()
        broker = await connected(terminal, server_time_offset=timedelta(hours=2))
        tick = await broker.tick("EURUSD")
        assert tick.time == T0 - timedelta(hours=2)
        await broker.close()

    async def test_pending_fill_on_the_server_is_reported(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        events = broker.events()
        await broker.submit(buy("maq-pend", type="LIMIT", price=1.0990))
        terminal.set_quote("EURUSD", 1.0985, 1.0987)
        await broker.poll()
        fills = [e for e in events.drain() if isinstance(e, OrderFilled)]
        assert [(f.client_order_id, f.opening, f.side) for f in fills] == [
            ("maq-pend", True, Side.BUY)
        ]

    async def test_pending_expiry_is_reported(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        events = broker.events()
        await broker.submit(
            buy("maq-exp", type="LIMIT", price=1.0990, expires_at=T0 + timedelta(minutes=30))
        )
        assert terminal.sent_requests[-1]["type_time"] == c.ORDER_TIME_SPECIFIED
        terminal.advance(minutes=31)
        await broker.poll()
        found = await broker.find_order("maq-exp")
        assert found is not None and found.status is OrderStatus.EXPIRED
        assert [e.kind for e in events.drain()] == ["broker.order_cancelled"]


class TestAccountAndData:
    async def test_account_kinds(self, setup: tuple[Mt5Broker, SimulatedTerminal]) -> None:
        broker, terminal = setup
        assert (await broker.account()).kind is AccountKind.DEMO
        terminal.account = dict(terminal.account or {}, trade_mode=c.ACCOUNT_TRADE_MODE_REAL)
        assert (await broker.account()).kind is AccountKind.REAL
        terminal.account = dict(terminal.account, trade_mode=9)
        assert (await broker.account()).kind is AccountKind.REAL  # unknown: strictest
        terminal.account = dict(terminal.account, trade_expert=False)
        assert not (await broker.account()).trade_allowed

    async def test_disabled_symbol(self, setup: tuple[Mt5Broker, SimulatedTerminal]) -> None:
        broker, terminal = setup
        terminal.symbols["EURUSD"]["trade_mode"] = c.SYMBOL_TRADE_MODE_CLOSEONLY
        assert not (await broker.symbol_spec("EURUSD")).trade_allowed
        result = await broker.submit(buy("maq-closeonly"))
        assert result.reject_reason is RejectReason.TRADE_DISABLED

    async def test_lost_terminal(self, setup: tuple[Mt5Broker, SimulatedTerminal]) -> None:
        broker, terminal = setup
        terminal.account = None
        with pytest.raises(BrokerConnectionError):
            await broker.account()

    async def test_bars_skip_the_forming_bar(
        self, setup: tuple[Mt5Broker, SimulatedTerminal]
    ) -> None:
        broker, terminal = setup
        bars = await broker.bars("EURUSD", Timeframe.M15, 5)
        assert bars[-1].close == pytest.approx(float(terminal.rates["EURUSD"][-2]["close"]))

    async def test_connect_without_the_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # None in sys.modules makes the import fail on every OS, even where the
        # real package (Windows) or the legacy tests' fake is importable.
        monkeypatch.setitem(sys.modules, "MetaTrader5", None)
        broker = Mt5Broker()
        with pytest.raises(BrokerConnectionError, match="MetaTrader5"):
            await broker.connect()

    async def test_initialize_failure(self) -> None:
        terminal = make_terminal()
        terminal.initialize = lambda *a, **k: False  # type: ignore[method-assign]
        terminal.error = (-6, "Terminal: Authorization failed")
        broker = Mt5Broker(terminal)
        with pytest.raises(BrokerConnectionError, match="Authorization failed"):
            await broker.connect()
