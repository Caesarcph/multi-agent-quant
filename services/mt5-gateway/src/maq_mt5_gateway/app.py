"""The gateway HTTP API: one broker behind a small, authenticated REST interface.

Every route except ``/health`` needs ``Authorization: Bearer <token>``.
Broker failures map onto status codes the client turns back into exceptions:

=====  ==========================  ===========================================
404    ``unknown_symbol``          ``UnknownSymbolError``
409    ``rejected``                ``BrokerRejection`` (reason, message, code)
502    ``broker_unavailable``      ``BrokerConnectionError`` (outcome unknown)
503    ``no_price``                ``NoPriceError``
=====  ==========================  ===========================================

Order rejections are not errors: ``POST /orders`` answers 200 with a REJECTED
``OrderResult``. Events are served by long polling (``GET /events?after=&wait=``),
which survives flaky networks and proxies better than a socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from maq_brokers.base import (
    Broker,
    BrokerConnectionError,
    BrokerRejection,
    NoPriceError,
    Subscription,
    UnknownSymbolError,
)
from maq_core.domain import (
    AccountState,
    Bar,
    ClosedTrade,
    Lots,
    OrderRequest,
    OrderResult,
    Position,
    SymbolSpec,
    Tick,
    Timeframe,
    WorkingOrder,
)
from maq_mt5_gateway import __version__


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_order_id: str
    volume: Lots | None = None


class ModifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sl: float | None = Field(default=None, gt=0)
    tp: float | None = Field(default=None, gt=0)


@dataclass
class EventLog:
    """Recent broker events with sequence numbers, for long-polling clients."""

    capacity: int = 1000
    _items: deque[tuple[int, dict[str, Any]]] = field(default_factory=deque)
    _seq: int = 0
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def add(self, event: dict[str, Any]) -> None:
        async with self._changed:
            self._seq += 1
            self._items.append((self._seq, event))
            while len(self._items) > self.capacity:
                self._items.popleft()
            self._changed.notify_all()

    async def after(self, seq: int, wait: float) -> tuple[list[dict[str, Any]], int]:
        async with self._changed:
            if self._seq <= seq and wait > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait_for(lambda: self._seq > seq), wait)
            return [event for n, event in self._items if n > seq], self._seq


def create_app(
    broker: Broker,
    *,
    token: str,
    poll: Callable[[], Awaitable[object]] | None = None,
    poll_interval: float = 1.0,
    event_capacity: int = 1000,
) -> FastAPI:
    """Build the API around a connected broker.

    ``poll`` (e.g. ``Mt5Broker.poll``) is called every ``poll_interval`` seconds
    in the background so server-side fills and closes become events.
    """
    if len(token) < 16:
        raise ValueError("the gateway token must be at least 16 characters")
    events = EventLog(event_capacity)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        subscription = broker.events()
        tasks = [asyncio.create_task(_forward(subscription, events))]
        if poll is not None:
            tasks.append(asyncio.create_task(_keep_polling(poll, poll_interval)))
        try:
            yield
        finally:
            subscription.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="maq MT5 gateway", version=__version__, lifespan=lifespan)
    app.state.events = events

    async def authorized(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")

    auth = [Depends(authorized)]

    @app.exception_handler(UnknownSymbolError)
    async def _unknown_symbol(_: Request, error: UnknownSymbolError) -> JSONResponse:
        return JSONResponse({"error": "unknown_symbol", "detail": str(error)}, status_code=404)

    @app.exception_handler(NoPriceError)
    async def _no_price(_: Request, error: NoPriceError) -> JSONResponse:
        return JSONResponse({"error": "no_price", "detail": str(error)}, status_code=503)

    @app.exception_handler(BrokerRejection)
    async def _rejected(_: Request, error: BrokerRejection) -> JSONResponse:
        body = {
            "error": "rejected",
            "reason": error.reason.value,
            "message": error.message,
            "broker_code": error.broker_code,
        }
        return JSONResponse(body, status_code=409)

    @app.exception_handler(BrokerConnectionError)
    async def _unavailable(_: Request, error: BrokerConnectionError) -> JSONResponse:
        return JSONResponse({"error": "broker_unavailable", "detail": str(error)}, status_code=502)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "broker": broker.name, "version": __version__}

    @app.get("/account", dependencies=auth)
    async def account() -> AccountState:
        return await broker.account()

    @app.get("/symbols/{symbol}", dependencies=auth)
    async def symbol_spec(symbol: str) -> SymbolSpec:
        return await broker.symbol_spec(symbol)

    @app.get("/ticks/{symbol}", dependencies=auth)
    async def tick(symbol: str) -> Tick:
        return await broker.tick(symbol)

    @app.get("/bars/{symbol}", dependencies=auth)
    async def bars(
        symbol: str, timeframe: Timeframe, count: Annotated[int, Query(gt=0, le=10_000)]
    ) -> list[Bar]:
        return await broker.bars(symbol, timeframe, count)

    @app.get("/positions", dependencies=auth)
    async def positions(symbol: str | None = None) -> list[Position]:
        return await broker.positions(symbol)

    @app.get("/orders/working", dependencies=auth)
    async def working_orders(symbol: str | None = None) -> list[WorkingOrder]:
        return await broker.working_orders(symbol)

    @app.get("/trades/closed", dependencies=auth)
    async def closed_trades(since: datetime | None = None) -> list[ClosedTrade]:
        return await broker.closed_trades(since)

    @app.post("/orders", dependencies=auth)
    async def submit(order: OrderRequest) -> OrderResult:
        return await broker.submit(order)

    @app.get("/orders/{client_order_id}", dependencies=auth)
    async def find_order(client_order_id: str) -> OrderResult:
        result = await broker.find_order(client_order_id)
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "order_not_found"})
        return result

    @app.delete("/orders/{client_order_id}", dependencies=auth)
    async def cancel(client_order_id: str) -> OrderResult:
        return await broker.cancel(client_order_id)

    @app.post("/positions/{position_id}/close", dependencies=auth)
    async def close_position(position_id: str, body: CloseRequest) -> OrderResult:
        return await broker.close_position(
            position_id, client_order_id=body.client_order_id, volume=body.volume
        )

    @app.post("/positions/{position_id}/modify", dependencies=auth)
    async def modify_position(position_id: str, body: ModifyRequest) -> Position:
        return await broker.modify_position(position_id, sl=body.sl, tp=body.tp)

    @app.get("/events", dependencies=auth)
    async def read_events(
        after: Annotated[int, Query(ge=0)] = 0,
        wait: Annotated[float, Query(ge=0, le=60)] = 0,
    ) -> dict[str, Any]:
        items, last = await events.after(after, wait)
        return {"events": items, "last": last}

    return app


async def _forward(subscription: Subscription, events: EventLog) -> None:
    async for event in subscription:
        await events.add(event.model_dump(mode="json"))


async def _keep_polling(poll: Callable[[], Awaitable[object]], interval: float) -> None:
    while True:
        # A lost terminal is reported by the next request; keep trying.
        with contextlib.suppress(BrokerConnectionError):
            await poll()
        await asyncio.sleep(interval)
