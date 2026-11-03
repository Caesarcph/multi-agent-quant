"""``Mt5GatewayBroker``: the ``Broker`` interface over the MT5 Gateway's HTTP API.

Transport failures become ``BrokerConnectionError`` (outcome unknown: the order
manager will look the order up instead of resending it). Gateway error bodies
become the same exceptions the broker raised on the other side. Events arrive
by long polling: ``connect()`` starts a background task when
``follow_events`` is true; tests call ``poll_events()`` directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import TypeAdapter

from maq_brokers import events as _broker_events  # noqa: F401  (registers event kinds)
from maq_brokers.base import (
    BrokerConnectionError,
    BrokerError,
    BrokerRejection,
    EventHub,
    NoPriceError,
    Subscription,
    UnknownSymbolError,
)
from maq_brokers.events import BrokerEvent, ConnectionChanged
from maq_core.domain import (
    AccountState,
    Bar,
    ClosedTrade,
    OrderRequest,
    OrderResult,
    Position,
    RejectReason,
    SymbolSpec,
    Tick,
    Timeframe,
    WorkingOrder,
)
from maq_core.events import parse_event

log = logging.getLogger(__name__)

_BARS = TypeAdapter(list[Bar])
_POSITIONS = TypeAdapter(list[Position])
_WORKING = TypeAdapter(list[WorkingOrder])
_TRADES = TypeAdapter(list[ClosedTrade])


class Mt5GatewayBroker:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
        follow_events: bool = True,
        event_wait: float = 20.0,
        name: str = "mt5-gateway",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._timeout = timeout
        self._follow = follow_events
        self._event_wait = event_wait
        self._name = name
        self._client: httpx.AsyncClient | None = None
        self._hub = EventHub()
        self._last_event = 0
        self._follower: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return self._name

    def events(self) -> Subscription:
        return self._hub.subscribe()

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self._timeout,
            transport=self._transport,
        )
        await self._get("/account")  # checks reachability and the token
        # Only events from now on: skip what the gateway buffered before.
        _, self._last_event = await self._fetch_events(wait=0)
        if self._follow:
            self._follower = asyncio.create_task(self._follow_events())

    async def close(self) -> None:
        if self._follower is not None:
            self._follower.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._follower
        if self._client is not None:
            await self._client.aclose()
        self._hub.close()

    # ------------------------------------------------------------------ Broker
    async def account(self) -> AccountState:
        return AccountState.model_validate(await self._get("/account"))

    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        return SymbolSpec.model_validate(await self._get(f"/symbols/{symbol}"))

    async def tick(self, symbol: str) -> Tick:
        return Tick.model_validate(await self._get(f"/ticks/{symbol}"))

    async def bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        data = await self._get(
            f"/bars/{symbol}", params={"timeframe": timeframe.value, "count": count}
        )
        return _BARS.validate_python(data)

    async def positions(self, symbol: str | None = None) -> list[Position]:
        params = {"symbol": symbol} if symbol else None
        return _POSITIONS.validate_python(await self._get("/positions", params=params))

    async def working_orders(self, symbol: str | None = None) -> list[WorkingOrder]:
        params = {"symbol": symbol} if symbol else None
        return _WORKING.validate_python(await self._get("/orders/working", params=params))

    async def closed_trades(self, since: datetime | None = None) -> list[ClosedTrade]:
        params = {"since": since.isoformat()} if since else None
        return _TRADES.validate_python(await self._get("/trades/closed", params=params))

    async def submit(self, order: OrderRequest) -> OrderResult:
        data = await self._request("POST", "/orders", json=order.model_dump(mode="json"))
        return OrderResult.model_validate(data)

    async def find_order(self, client_order_id: str) -> OrderResult | None:
        try:
            data = await self._get(f"/orders/{client_order_id}")
        except _NotFound:
            return None
        return OrderResult.model_validate(data)

    async def cancel(self, client_order_id: str) -> OrderResult:
        return OrderResult.model_validate(
            await self._request("DELETE", f"/orders/{client_order_id}")
        )

    async def close_position(
        self, position_id: str, *, client_order_id: str, volume: float | None = None
    ) -> OrderResult:
        body = {"client_order_id": client_order_id, "volume": volume}
        data = await self._request("POST", f"/positions/{position_id}/close", json=body)
        return OrderResult.model_validate(data)

    async def modify_position(
        self, position_id: str, *, sl: float | None, tp: float | None
    ) -> Position:
        data = await self._request(
            "POST", f"/positions/{position_id}/modify", json={"sl": sl, "tp": tp}
        )
        return Position.model_validate(data)

    # ------------------------------------------------------------------ events
    async def poll_events(self, wait: float = 0.0) -> list[BrokerEvent]:
        """Fetch events the gateway has seen since the last call and publish them."""
        items, last = await self._fetch_events(wait=wait)
        self._last_event = max(self._last_event, last)
        received = []
        for item in items:
            event = parse_event(item)
            if isinstance(event, BrokerEvent):
                received.append(event)
                self._hub.publish(event)
        return received

    async def _fetch_events(self, wait: float) -> tuple[list[dict[str, Any]], int]:
        data = await self._get(
            "/events",
            params={"after": self._last_event, "wait": wait},
            timeout=self._timeout + wait,
        )
        return list(data["events"]), int(data["last"])

    async def _follow_events(self) -> None:
        connected = True
        while True:
            try:
                await self.poll_events(wait=self._event_wait)
                if not connected:
                    connected = True
                    self._hub.publish(ConnectionChanged(ts=_now(), connected=True))
            except BrokerConnectionError as error:
                if connected:
                    connected = False
                    log.warning("lost the gateway: %s", error)
                    self._hub.publish(
                        ConnectionChanged(ts=_now(), connected=False, detail=str(error))
                    )
                await asyncio.sleep(2.0)

    # ------------------------------------------------------------------ transport
    async def _get(
        self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        return await self._request("GET", path, params=params, timeout=timeout)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any:
        if self._client is None:
            raise BrokerConnectionError("not connected: call connect() first")
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except httpx.HTTPError as error:
            raise BrokerConnectionError(f"{method} {path}: {error!r}") from error
        if response.status_code < 400:
            return response.json()
        body = _json_or_text(response)
        detail = body.get("detail", body) if isinstance(body, dict) else body
        error_code = body.get("error") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            error_code = error_code or detail.get("error")
        if response.status_code == 404 and error_code == "unknown_symbol":
            raise UnknownSymbolError(path.rsplit("/", 1)[-1])
        if response.status_code == 404 and error_code == "order_not_found":
            raise _NotFound(path)
        if response.status_code == 409 and isinstance(body, dict):
            raise BrokerRejection(
                RejectReason(body.get("reason", "OTHER")),
                str(body.get("message", "")),
                body.get("broker_code"),
            )
        if response.status_code == 503 and error_code == "no_price":
            raise NoPriceError(str(detail))
        if response.status_code in (401, 403):
            raise BrokerError(f"the gateway refused the token ({response.status_code})")
        if response.status_code >= 500:
            raise BrokerConnectionError(f"{method} {path}: {response.status_code} {detail}")
        raise BrokerError(f"{method} {path}: {response.status_code} {detail}")


class _NotFound(BrokerError):
    pass


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
