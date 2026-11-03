"""The gateway end to end: client -> HTTP -> gateway -> Mt5Broker -> simulated server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from maq_brokers import (
    BrokerConnectionError,
    BrokerError,
    ConnectionChanged,
    NoPriceError,
    OrderFilled,
    UnknownSymbolError,
)
from maq_brokers.gateway import Mt5GatewayBroker
from maq_brokers.mt5 import Mt5Broker
from maq_brokers.testing import ASK, BID, BrokerContract, BrokerHarness
from maq_core.clock import ManualClock
from maq_core.domain import OrderRequest
from maq_mt5_gateway.app import EventLog, create_app
from maq_mt5_gateway.cli import main, parse_args
from tests.fakes.mt5_server import SimulatedTerminal

T0 = datetime(2026, 11, 3, 12, tzinfo=UTC)
TOKEN = "test-token-0123456789"


@dataclass
class Stack:
    terminal: SimulatedTerminal
    server_broker: Mt5Broker
    client: Mt5GatewayBroker
    app: Any


@pytest.fixture
async def stack() -> AsyncIterator[Stack]:
    async with running_stack() as built:
        yield built


class running_stack:
    def __init__(self, *, token: str = TOKEN, follow_events: bool = False) -> None:
        self.token = token
        self.follow = follow_events

    async def __aenter__(self) -> Stack:
        terminal = SimulatedTerminal(now=T0)
        terminal.add_symbol("EURUSD", bid=BID, ask=ASK)
        terminal.set_rates("EURUSD", [1.1 + i * 0.0001 for i in range(30)])
        server_broker = Mt5Broker(terminal, clock=ManualClock(T0))
        await server_broker.connect()
        app = create_app(server_broker, token=TOKEN)
        self._lifespan = app.router.lifespan_context(app)
        await self._lifespan.__aenter__()
        client = Mt5GatewayBroker(
            "http://gateway",
            token=self.token,
            transport=httpx.ASGITransport(app=app),
            follow_events=self.follow,
            event_wait=0.05,
        )
        self.stack = Stack(terminal, server_broker, client, app)
        return self.stack

    async def __aexit__(self, *exc: object) -> None:
        await self.stack.client.close()
        await self._lifespan.__aexit__(None, None, None)
        await self.stack.server_broker.close()


@dataclass
class GatewayHarness:
    stack: Stack
    symbol: str = "EURUSD"

    @property
    def broker(self) -> Mt5GatewayBroker:
        return self.stack.client

    def set_quote(self, bid: float, ask: float) -> None:
        self.stack.terminal.set_quote(self.symbol, bid, ask)

    async def settle(self) -> None:
        await self.stack.server_broker.poll()  # the gateway's own poller, run by hand
        for _ in range(10):  # let the gateway forward the events into its log
            await asyncio.sleep(0)
        await self.stack.client.poll_events()


class TestGatewayContract(BrokerContract):
    @pytest.fixture
    async def harness(self) -> AsyncIterator[BrokerHarness]:
        async with running_stack() as built:
            await built.client.connect()
            yield GatewayHarness(built)


class TestAuth:
    async def test_health_needs_no_token(self, stack: Stack) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stack.app), base_url="http://gateway"
        ) as raw:
            response = await raw.get("/health")
            assert response.status_code == 200
            assert response.json()["broker"] == "mt5"
            assert (await raw.get("/account")).status_code == 401

    async def test_wrong_token_is_refused(self) -> None:
        async with running_stack(token="wrong-token-0123456789") as built:
            with pytest.raises(BrokerError, match="refused the token"):
                await built.client.connect()

    def test_short_tokens_are_not_allowed(self) -> None:
        with pytest.raises(ValueError, match="16 characters"):
            create_app(object(), token="short")  # type: ignore[arg-type]


class TestErrors:
    async def test_errors_cross_the_wire_as_the_same_exceptions(self, stack: Stack) -> None:
        await stack.client.connect()
        with pytest.raises(UnknownSymbolError):
            await stack.client.symbol_spec("NOSUCH")
        stack.terminal.symbols["EURUSD"]["bid"] = 0.0
        with pytest.raises(NoPriceError):
            await stack.client.tick("EURUSD")
        stack.terminal.account = None
        with pytest.raises(BrokerConnectionError, match="502"):
            await stack.client.account()

    async def test_unreachable_gateway(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = Mt5GatewayBroker(
            "http://gateway", token=TOKEN, transport=httpx.MockTransport(refuse)
        )
        with pytest.raises(BrokerConnectionError, match="connection refused"):
            await client.connect()
        await client.close()

    async def test_not_connected(self) -> None:
        client = Mt5GatewayBroker("http://gateway", token=TOKEN)
        with pytest.raises(BrokerConnectionError, match="connect"):
            await client.account()

    async def test_unknown_order_is_none(self, stack: Stack) -> None:
        await stack.client.connect()
        assert await stack.client.find_order("maq-never") is None


class TestEvents:
    async def test_events_only_from_connect_onwards(self, stack: Stack) -> None:
        await stack.server_broker.submit(
            OrderRequest(client_order_id="maq-before", symbol="EURUSD", side="BUY", volume=0.1)
        )
        for _ in range(10):
            await asyncio.sleep(0)
        await stack.client.connect()
        assert await stack.client.poll_events() == []

    async def test_background_follower_delivers_events(self) -> None:
        async with running_stack(follow_events=True) as built:
            await built.client.connect()
            events = built.client.events()
            await built.client.submit(
                OrderRequest(client_order_id="maq-follow", symbol="EURUSD", side="BUY", volume=0.1)
            )
            event = await events.get(timeout=2)
            assert isinstance(event, OrderFilled)
            assert event.client_order_id == "maq-follow"

    async def test_follower_reports_a_lost_gateway(self, stack: Stack) -> None:
        calls = {"n": 0}
        inner = httpx.ASGITransport(app=stack.app)

        async def flaky(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if request.url.path == "/events" and calls["n"] > 3:
                raise httpx.ReadTimeout("gateway gone", request=request)
            return await inner.handle_async_request(request)

        client = Mt5GatewayBroker(
            "http://gateway", token=TOKEN, transport=httpx.MockTransport(flaky), event_wait=0.01
        )
        await client.connect()
        events = client.events()
        lost = await events.get(timeout=2)
        assert isinstance(lost, ConnectionChanged)
        assert not lost.connected
        await client.close()

    async def test_event_log_long_poll_and_capacity(self) -> None:
        log = EventLog(capacity=2)
        waiter = asyncio.create_task(log.after(0, wait=2))
        await asyncio.sleep(0)
        await log.add({"n": 1})
        items, last = await waiter
        assert (items, last) == ([{"n": 1}], 1)
        await log.add({"n": 2})
        await log.add({"n": 3})
        items, last = await log.after(0, wait=0)
        assert (items, last) == ([{"n": 2}, {"n": 3}], 3)
        assert await log.after(3, wait=0.01) == ([], 3)


class TestCli:
    def test_refuses_to_start_without_a_token(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("MAQ_GATEWAY_TOKEN", raising=False)
        assert main([]) == 2
        assert "MAQ_GATEWAY_TOKEN" in capsys.readouterr().err

    def test_reports_a_missing_terminal(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MAQ_GATEWAY_TOKEN", TOKEN)
        monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", None)
        assert main(["--port", "0"]) == 1
        assert "Cannot connect to MetaTrader 5" in capsys.readouterr().err

    def test_defaults(self) -> None:
        args = parse_args([])
        assert (args.host, args.port, args.poll_interval) == ("127.0.0.1", 8765, 1.0)
