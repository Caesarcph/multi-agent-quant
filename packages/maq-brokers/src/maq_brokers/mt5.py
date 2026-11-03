"""MetaTrader 5 adapter: the ``Broker`` interface over the official ``MetaTrader5`` package.

It runs on the Windows machine next to the terminal, either inside the MT5
Gateway service or directly. Everything else talks to it through the
``Broker`` interface (locally or via ``Mt5GatewayBroker``).

* **One worker thread.** The package keeps process-wide state and is not
  thread-safe, so every call goes through a single-thread executor.
* **Idempotency.** The client order id goes into the order comment (MT5 allows
  31 characters; the id is at most that). ``submit`` and ``close_position``
  look for an existing order with that comment before sending anything, and
  ``find_order`` finds it again after a restart. Some brokers shorten
  comments; a comment of 12+ characters that is a prefix of the id matches too.
* **Checks before sending.** ``order_check`` runs before ``order_send``; an
  unsupported filling type is retried with the next allowed one.
* **Events from the deal history.** ``poll()`` turns deals it has not seen yet
  into ``OrderFilled`` / ``PositionClosed`` events, so stops hit on the server,
  manual closes and pending fills are reported too, and a restart does not
  replay old deals.
* **Server time.** MT5 reports times in the trade server's zone as if they were
  UTC. Pass ``server_time_offset`` (server minus UTC, e.g. +2h or +3h for most
  FX brokers) to get real UTC timestamps.

Without Windows the adapter is exercised against ``tests/fakes/mt5_server.py``,
a simulated trade server, through the same contract suite as ``PaperBroker``.
"""

from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from maq_brokers.base import (
    BrokerConnectionError,
    BrokerRejection,
    EventHub,
    NoPriceError,
    Subscription,
    UnknownSymbolError,
)
from maq_brokers.events import (
    BrokerEvent,
    ConnectionChanged,
    OrderCancelled,
    OrderFilled,
    PositionClosed,
)
from maq_core.clock import Clock, SystemClock
from maq_core.domain import (
    CLIENT_ORDER_ID_MAX_LENGTH,
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

# Trade server return codes (stable MQL5 values).
DONE, PLACED, DONE_PARTIAL = 10009, 10008, 10010
INVALID_FILL = 10030
_UNKNOWN_OUTCOME = {10012, 10031}  # timeout, no connection: ask find_order
RETCODE_REASONS: dict[int, RejectReason] = {
    10004: RejectReason.PRICE_CHANGED,  # requote
    10006: RejectReason.OTHER,  # rejected
    10007: RejectReason.OTHER,  # cancelled by the trader
    10011: RejectReason.OTHER,  # processing error
    10013: RejectReason.INVALID_REQUEST,
    10014: RejectReason.INVALID_VOLUME,
    10015: RejectReason.INVALID_PRICE,
    10016: RejectReason.INVALID_STOPS,
    10017: RejectReason.TRADE_DISABLED,
    10018: RejectReason.MARKET_CLOSED,
    10019: RejectReason.NO_MONEY,
    10020: RejectReason.PRICE_CHANGED,
    10021: RejectReason.PRICE_CHANGED,  # no quotes
    10022: RejectReason.INVALID_REQUEST,  # invalid expiration
    10024: RejectReason.OTHER,  # too frequent requests
    10026: RejectReason.TRADE_DISABLED,  # algo trading disabled by the server
    10027: RejectReason.TRADE_DISABLED,  # algo trading disabled in the terminal
    10030: RejectReason.INVALID_REQUEST,  # unsupported filling type
    10033: RejectReason.OTHER,  # pending order limit
    10034: RejectReason.INVALID_VOLUME,  # volume limit for the symbol
    10035: RejectReason.INVALID_REQUEST,  # invalid order type
    10036: RejectReason.NOT_FOUND,  # position already closed
    10038: RejectReason.INVALID_VOLUME,  # close volume exceeds the position
    10040: RejectReason.OTHER,  # position limit
    10042: RejectReason.TRADE_DISABLED,  # long only
    10043: RejectReason.TRADE_DISABLED,  # short only
    10044: RejectReason.TRADE_DISABLED,  # close only
}

# Numeric values from the MQL5 enums, used when reading records.
_POSITION_BUY = 0
_DEAL_BUY, _DEAL_SELL = 0, 1
_ENTRY_IN, _ENTRY_OUT, _ENTRY_OUT_BY = 0, 1, 3
_REASON_EXPERT = 3
_DEAL_REASONS = {3: CloseReason.CLIENT, 4: CloseReason.STOP_LOSS, 5: CloseReason.TAKE_PROFIT,
                 6: CloseReason.STOP_OUT}  # fmt: skip
_ACCOUNT_KINDS = {0: AccountKind.DEMO, 1: AccountKind.CONTEST, 2: AccountKind.REAL}
_ORDER_TYPES = {  # ORDER_TYPE_* -> (side, type)
    0: (Side.BUY, OrderType.MARKET),
    1: (Side.SELL, OrderType.MARKET),
    2: (Side.BUY, OrderType.LIMIT),
    3: (Side.SELL, OrderType.LIMIT),
    4: (Side.BUY, OrderType.STOP),
    5: (Side.SELL, OrderType.STOP),
}
_ORDER_STATES = {
    0: OrderStatus.SUBMITTED,  # started
    1: OrderStatus.ACCEPTED,  # placed
    2: OrderStatus.CANCELLED,
    3: OrderStatus.PARTIALLY_FILLED,
    4: OrderStatus.FILLED,
    5: OrderStatus.REJECTED,
    6: OrderStatus.EXPIRED,
}
_KEY = re.compile(rf"^[A-Za-z0-9._:-]{{1,{CLIENT_ORDER_ID_MAX_LENGTH}}}$")


def comment_matches(comment: str, key: str) -> bool:
    """Whether an MT5 comment carries ``key`` (allowing for brokers that shorten it)."""
    return comment == key or (len(comment) >= 12 and key.startswith(comment))


class Mt5Broker:
    def __init__(
        self,
        mt5: Any | None = None,
        *,
        terminal_path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout_ms: int = 30_000,
        clock: Clock | None = None,
        history_window: timedelta = timedelta(days=7),
        server_time_offset: timedelta = timedelta(0),
        name: str = "mt5",
    ) -> None:
        self._mt5 = mt5
        self._terminal_path = terminal_path
        self._login, self._password, self._server = login, password, server
        self._timeout_ms = timeout_ms
        self._clock = clock or SystemClock()
        self._window = history_window
        self._offset = server_time_offset
        self._name = name
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._hub = EventHub()
        self._results: dict[str, OrderResult] = {}
        self._seen_deals: set[int] = set()
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._connected

    def events(self) -> Subscription:
        return self._hub.subscribe()

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        if self._mt5 is None:
            try:
                import MetaTrader5  # type: ignore[import-not-found]
            except ImportError as error:
                raise BrokerConnectionError(
                    "the MetaTrader5 package is not installed (it only exists for Windows)"
                ) from error
            self._mt5 = MetaTrader5
        kwargs: dict[str, Any] = {"timeout": self._timeout_ms}
        if self._login is not None:
            kwargs.update(login=self._login, password=self._password, server=self._server)
        args = (self._terminal_path,) if self._terminal_path else ()
        if not await self._call("initialize", *args, **kwargs):
            raise BrokerConnectionError(f"MT5 initialize failed: {await self._last_error()}")
        self._connected = True
        # Deals that exist already are history, not news.
        self._seen_deals.update(int(d.ticket) for d in await self._history_deals())
        self._publish(ConnectionChanged(ts=self._clock.now(), connected=True))

    async def close(self) -> None:
        if self._connected:
            await self._call("shutdown")
            self._connected = False
            self._publish(ConnectionChanged(ts=self._clock.now(), connected=False))
        self._hub.close()
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------ market data
    async def symbol_spec(self, symbol: str) -> SymbolSpec:
        info = await self._symbol_info(symbol)
        tick_value = float(info.trade_tick_value)
        if tick_value <= 0:
            raise NoPriceError(f"{symbol} has no tick value yet (no quotes)")
        return SymbolSpec(
            symbol=info.name,
            digits=int(info.digits),
            point=float(info.point),
            tick_size=float(info.trade_tick_size or info.point),
            tick_value=tick_value,
            contract_size=float(info.trade_contract_size),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            stops_level=int(getattr(info, "trade_stops_level", 0)),
            freeze_level=int(getattr(info, "trade_freeze_level", 0)),
            currency_base=str(getattr(info, "currency_base", "")),
            currency_profit=str(getattr(info, "currency_profit", "")),
            currency_margin=str(getattr(info, "currency_margin", "")),
            trade_allowed=int(getattr(info, "trade_mode", 4)) == 4,
        )

    async def tick(self, symbol: str) -> Tick:
        await self._symbol_info(symbol)
        raw = await self._call("symbol_info_tick", symbol)
        if raw is None or not raw.bid or not raw.ask:
            raise NoPriceError(f"no quote for {symbol}")
        return Tick(symbol=symbol, time=self._time(raw), bid=float(raw.bid), ask=float(raw.ask))

    async def bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        """Completed bars: position 0 (the forming bar) is skipped."""
        if count <= 0:
            raise ValueError("count must be positive")
        await self._symbol_info(symbol)
        mt5_timeframe = getattr(self._require(), f"TIMEFRAME_{timeframe.value}")
        rates = await self._call("copy_rates_from_pos", symbol, mt5_timeframe, 1, count)
        if rates is None:
            raise BrokerConnectionError(f"copy_rates_from_pos failed: {await self._last_error()}")
        return [
            Bar(
                time=datetime.fromtimestamp(int(row["time"]), UTC) - self._offset,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=float(row["tick_volume"]),
                spread=int(row["spread"]),
                real_volume=float(row["real_volume"]),
            )
            for row in rates
        ]

    # ------------------------------------------------------------------ account
    async def account(self) -> AccountState:
        info = await self._call("account_info")
        if info is None:
            raise BrokerConnectionError(f"account_info failed: {await self._last_error()}")
        return AccountState(
            # An unknown trade mode is treated as real money: the strictest rules apply.
            kind=_ACCOUNT_KINDS.get(int(getattr(info, "trade_mode", 0)), AccountKind.REAL),
            currency=str(info.currency),
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            margin_free=float(info.margin_free),
            leverage=max(int(info.leverage), 1),
            login=int(info.login) if getattr(info, "login", None) is not None else None,
            server=str(getattr(info, "server", "")) or None,
            trade_allowed=bool(getattr(info, "trade_allowed", True))
            and bool(getattr(info, "trade_expert", True)),
        )

    async def positions(self, symbol: str | None = None) -> list[Position]:
        rows = await self._query("positions_get", **({"symbol": symbol} if symbol else {}))
        return [self._position(row) for row in rows]

    async def working_orders(self, symbol: str | None = None) -> list[WorkingOrder]:
        rows = await self._query("orders_get", **({"symbol": symbol} if symbol else {}))
        orders = []
        for row in rows:
            side, order_type = _ORDER_TYPES.get(int(row.type), (Side.BUY, OrderType.LIMIT))
            orders.append(
                WorkingOrder(
                    client_order_id=_key_or(row.comment, f"mt5-{row.ticket}"),
                    broker_order_id=str(row.ticket),
                    symbol=row.symbol,
                    side=side,
                    type=order_type,
                    volume=float(row.volume_current),
                    price=float(row.price_open),
                    sl=float(row.sl) or None,
                    tp=float(row.tp) or None,
                    placed_at=self._time(row, "time_setup"),
                    expires_at=(
                        datetime.fromtimestamp(int(row.time_expiration), UTC) - self._offset
                        if int(getattr(row, "time_expiration", 0) or 0)
                        else None
                    ),
                    magic=int(row.magic),
                )
            )
        return orders

    async def closed_trades(self, since: datetime | None = None) -> list[ClosedTrade]:
        deals = await self._history_deals(since)
        trades = []
        for deal in deals:
            if _is_exit(deal) and (since is None or self._time(deal) >= since):
                trades.append(await self._closed_trade(deal, deals))
        return trades

    # ------------------------------------------------------------------ orders
    async def submit(self, order: OrderRequest) -> OrderResult:
        existing = await self.find_order(order.client_order_id)
        if existing is not None:
            return existing
        try:
            info = await self._symbol_info(order.symbol)
        except UnknownSymbolError:
            return self._remember(
                _rejected(order.client_order_id, order.symbol, order.side, order.volume,
                          RejectReason.UNKNOWN_SYMBOL, f"unknown symbol {order.symbol!r}",
                          self._clock.now())
            )  # fmt: skip
        mt5 = self._require()
        pending = order.type is not OrderType.MARKET
        if pending:
            assert order.price is not None
            price = order.price
        else:
            raw_tick = await self._call("symbol_info_tick", order.symbol)
            if raw_tick is None or not raw_tick.ask:
                return self._remember(
                    _rejected(order.client_order_id, order.symbol, order.side, order.volume,
                              RejectReason.MARKET_CLOSED, "no quote", self._clock.now())
                )  # fmt: skip
            market = float(raw_tick.ask if order.side is Side.BUY else raw_tick.bid)
            price = order.price if order.price is not None else market
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING if pending else mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": self._order_type(order),
            "price": price,
            "sl": order.sl or 0.0,
            "tp": order.tp or 0.0,
            "deviation": order.max_slippage_points,
            "magic": order.magic,
            "comment": order.client_order_id,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if order.expires_at is not None:
            request["type_time"] = mt5.ORDER_TIME_SPECIFIED
            request["expiration"] = int((order.expires_at + self._offset).timestamp())
        filling = [mt5.ORDER_FILLING_RETURN] if pending else self._filling_types(info)
        result = await self._send(request, filling, order.client_order_id, order.symbol,
                                  order.side, order.volume)  # fmt: skip
        await self.poll()
        return result

    async def find_order(self, client_order_id: str) -> OrderResult | None:
        cached = self._results.get(client_order_id)
        if cached is not None and cached.status.is_terminal:
            return cached
        for row in await self._query("orders_get"):
            if comment_matches(str(row.comment), client_order_id):
                side, _ = _ORDER_TYPES.get(int(row.type), (Side.BUY, OrderType.LIMIT))
                return self._remember(
                    OrderResult(
                        client_order_id=client_order_id,
                        status=OrderStatus.ACCEPTED,
                        symbol=row.symbol,
                        side=side,
                        requested_volume=float(row.volume_initial),
                        time=self._time(row, "time_setup"),
                        broker_order_id=str(row.ticket),
                    )
                )
        start, end = self._window_bounds()
        history = await self._call("history_orders_get", start, end)
        matches = [
            row for row in history or () if comment_matches(str(row.comment), client_order_id)
        ]
        if matches:
            return self._remember(await self._from_history(client_order_id, matches[-1]))
        return cached

    async def cancel(self, client_order_id: str) -> OrderResult:
        mt5 = self._require()
        for row in await self._query("orders_get"):
            if comment_matches(str(row.comment), client_order_id):
                response = await self._call(
                    "order_send", {"action": mt5.TRADE_ACTION_REMOVE, "order": int(row.ticket)}
                )
                if response is None:
                    raise BrokerConnectionError(f"order_send failed: {await self._last_error()}")
                if response.retcode != DONE:
                    raise BrokerRejection(
                        RETCODE_REASONS.get(response.retcode, RejectReason.OTHER),
                        str(response.comment),
                        str(response.retcode),
                    )
                side, _ = _ORDER_TYPES.get(int(row.type), (Side.BUY, OrderType.LIMIT))
                result = self._remember(
                    OrderResult(
                        client_order_id=client_order_id,
                        status=OrderStatus.CANCELLED,
                        symbol=row.symbol,
                        side=side,
                        requested_volume=float(row.volume_initial),
                        time=self._clock.now(),
                        broker_order_id=str(row.ticket),
                    )
                )
                self._publish(
                    OrderCancelled(
                        ts=self._clock.now(), client_order_id=client_order_id, symbol=row.symbol
                    )
                )
                return result
        known = await self.find_order(client_order_id)
        if known is not None and known.status is OrderStatus.CANCELLED:
            return known
        raise BrokerRejection(RejectReason.NOT_FOUND, f"no working order {client_order_id!r}")

    async def close_position(
        self, position_id: str, *, client_order_id: str, volume: float | None = None
    ) -> OrderResult:
        existing = await self.find_order(client_order_id)
        if existing is not None:
            return existing
        row = await self._position_row(position_id)
        mt5 = self._require()
        info = await self._symbol_info(row.symbol)
        position_side = Side.BUY if int(row.type) == _POSITION_BUY else Side.SELL
        close_side = position_side.opposite
        lots = float(row.volume) if volume is None else volume
        raw_tick = await self._call("symbol_info_tick", row.symbol)
        if raw_tick is None:
            raise BrokerConnectionError(f"no quote for {row.symbol}")
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(row.ticket),
            "symbol": row.symbol,
            "volume": lots,
            "type": mt5.ORDER_TYPE_SELL if close_side is Side.SELL else mt5.ORDER_TYPE_BUY,
            "price": float(raw_tick.bid if close_side is Side.SELL else raw_tick.ask),
            "deviation": 20,
            "magic": int(row.magic),
            "comment": client_order_id,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = await self._send(request, self._filling_types(info), client_order_id,
                                  row.symbol, close_side, lots, position_id=position_id)  # fmt: skip
        await self.poll()
        return result

    async def modify_position(
        self, position_id: str, *, sl: float | None, tp: float | None
    ) -> Position:
        row = await self._position_row(position_id)
        spec = await self.symbol_spec(row.symbol)
        mt5 = self._require()
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(row.ticket),
            "symbol": row.symbol,
            "sl": spec.normalize_price(sl) if sl is not None else 0.0,
            "tp": spec.normalize_price(tp) if tp is not None else 0.0,
            "magic": int(row.magic),
        }
        response = await self._call("order_send", request)
        if response is None:
            raise BrokerConnectionError(f"order_send failed: {await self._last_error()}")
        if response.retcode != DONE:
            raise BrokerRejection(
                RETCODE_REASONS.get(response.retcode, RejectReason.OTHER),
                str(response.comment),
                str(response.retcode),
            )
        return self._position(await self._position_row(position_id))

    # ------------------------------------------------------------------ events
    async def poll(self) -> list[BrokerEvent]:
        """Publish fills and closes the server reported since the last poll."""
        deals = await self._history_deals()
        fresh = sorted(
            (d for d in deals if int(d.ticket) not in self._seen_deals and _is_trade(d)),
            key=lambda d: (int(getattr(d, "time_msc", 0) or int(d.time) * 1000), int(d.ticket)),
        )
        events: list[BrokerEvent] = []
        for deal in fresh:
            self._seen_deals.add(int(deal.ticket))
            ours = str(deal.comment) if int(deal.reason) == _REASON_EXPERT else ""
            events.append(
                OrderFilled(
                    ts=self._clock.now(),
                    client_order_id=ours if _KEY.match(ours) else None,
                    position_id=str(deal.position_id),
                    symbol=deal.symbol,
                    side=Side.BUY if int(deal.type) == _DEAL_BUY else Side.SELL,
                    volume=float(deal.volume),
                    price=float(deal.price),
                    commission=float(deal.commission),
                    opening=int(deal.entry) == _ENTRY_IN,
                )
            )
            if _is_exit(deal):
                trade = await self._closed_trade(deal, deals)
                events.append(PositionClosed(ts=self._clock.now(), trade=trade))
        for key, cached in list(self._results.items()):
            if cached.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
                fresh_result = await self.find_order(key)
                if fresh_result is not None and fresh_result.status in (
                    OrderStatus.CANCELLED,
                    OrderStatus.EXPIRED,
                ):
                    events.append(
                        OrderCancelled(
                            ts=self._clock.now(),
                            client_order_id=key,
                            symbol=fresh_result.symbol,
                            expired=fresh_result.status is OrderStatus.EXPIRED,
                        )
                    )
        for event in events:
            self._publish(event)
        return events

    # ------------------------------------------------------------------ internals
    def _require(self) -> Any:
        if self._mt5 is None:
            raise BrokerConnectionError("not connected: call connect() first")
        return self._mt5

    async def _call(self, function: str, *args: Any, **kwargs: Any) -> Any:
        target: Callable[..., Any] = getattr(self._require(), function)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, functools.partial(target, *args, **kwargs)
        )

    async def _last_error(self) -> str:
        try:
            code, message = await self._call("last_error")
        except Exception:
            return "unknown error"
        return f"{code} {message}"

    async def _query(self, function: str, **filters: Any) -> Sequence[Any]:
        rows = await self._call(function, **filters)
        if rows is None:
            raise BrokerConnectionError(f"{function} failed: {await self._last_error()}")
        return list(rows)

    async def _symbol_info(self, symbol: str) -> Any:
        if not await self._call("symbol_select", symbol, True):
            raise UnknownSymbolError(symbol)
        info = await self._call("symbol_info", symbol)
        if info is None:
            raise BrokerConnectionError(f"symbol_info failed: {await self._last_error()}")
        return info

    async def _position_row(self, position_id: str) -> Any:
        try:
            ticket = int(position_id)
        except ValueError:
            raise BrokerRejection(RejectReason.NOT_FOUND, f"no position {position_id!r}") from None
        rows = await self._query("positions_get", ticket=ticket)
        if not rows:
            raise BrokerRejection(RejectReason.NOT_FOUND, f"no open position {position_id!r}")
        return rows[0]

    def _window_bounds(self, since: datetime | None = None) -> tuple[datetime, datetime]:
        now = self._clock.now() + self._offset
        start = (since + self._offset) if since is not None else now - self._window
        return start - timedelta(days=1), now + timedelta(days=2)

    async def _history_deals(self, since: datetime | None = None) -> list[Any]:
        start, end = self._window_bounds(since)
        deals = await self._call("history_deals_get", start, end)
        if deals is None:
            raise BrokerConnectionError(f"history_deals_get failed: {await self._last_error()}")
        return list(deals)

    async def _send(
        self,
        request: dict[str, Any],
        filling_types: Sequence[int],
        key: str,
        symbol: str,
        side: Side,
        volume: float,
        *,
        position_id: str | None = None,
    ) -> OrderResult:
        now = self._clock.now()
        response: Any = None
        for filling in filling_types:
            request["type_filling"] = filling
            check = await self._call("order_check", request)
            if check is None:
                raise BrokerConnectionError(f"order_check failed: {await self._last_error()}")
            if int(check.retcode) not in (0, DONE):
                if int(check.retcode) == INVALID_FILL:
                    continue
                reason = RETCODE_REASONS.get(int(check.retcode), RejectReason.OTHER)
                return self._remember(
                    _rejected(key, symbol, side, volume, reason, str(check.comment), now,
                              str(check.retcode))
                )  # fmt: skip
            response = await self._call("order_send", request)
            if response is None:
                raise BrokerConnectionError(f"order_send got no answer: {await self._last_error()}")
            if int(response.retcode) != INVALID_FILL:
                break
        if response is None:
            return self._remember(
                _rejected(key, symbol, side, volume, RejectReason.INVALID_REQUEST,
                          "no filling type accepted", now, str(INVALID_FILL))
            )  # fmt: skip
        code = int(response.retcode)
        if code in _UNKNOWN_OUTCOME:
            raise BrokerConnectionError(f"order_send: {code} {response.comment}")
        if code == PLACED:
            return self._remember(
                OrderResult(
                    client_order_id=key,
                    status=OrderStatus.ACCEPTED,
                    symbol=symbol,
                    side=side,
                    requested_volume=volume,
                    time=now,
                    broker_order_id=str(response.order),
                )
            )
        if code in (DONE, DONE_PARTIAL):
            filled = float(response.volume) or volume
            position = position_id or await self._position_of_deal(int(response.deal))
            return self._remember(
                OrderResult(
                    client_order_id=key,
                    status=OrderStatus.FILLED if code == DONE else OrderStatus.PARTIALLY_FILLED,
                    symbol=symbol,
                    side=side,
                    requested_volume=volume,
                    filled_volume=min(filled, volume),
                    avg_fill_price=float(response.price),
                    time=now,
                    broker_order_id=str(response.order),
                    position_id=position or str(response.order),
                )
            )
        reason = RETCODE_REASONS.get(code, RejectReason.OTHER)
        return self._remember(
            _rejected(key, symbol, side, volume, reason, str(response.comment), now, str(code))
        )

    async def _position_of_deal(self, deal_ticket: int) -> str | None:
        if not deal_ticket:
            return None
        deals = await self._call("history_deals_get", ticket=deal_ticket)
        for deal in deals or ():
            return str(deal.position_id)
        return None

    async def _from_history(self, key: str, row: Any) -> OrderResult:
        side, _ = _ORDER_TYPES.get(int(row.type), (Side.BUY, OrderType.MARKET))
        status = _ORDER_STATES.get(int(row.state), OrderStatus.SUBMITTED)
        common: dict[str, Any] = {
            "client_order_id": key,
            "symbol": row.symbol,
            "side": side,
            "requested_volume": float(row.volume_initial),
            "time": self._time(row, "time_done") if getattr(row, "time_done", 0) else
            self._time(row, "time_setup"),
            "broker_order_id": str(row.ticket),
        }  # fmt: skip
        if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            deals = await self._call("history_deals_get", position=int(row.position_id)) or ()
            fills = [d for d in deals if int(d.order) == int(row.ticket)]
            volume = sum(float(d.volume) for d in fills)
            price = (
                sum(float(d.price) * float(d.volume) for d in fills) / volume
                if volume
                else float(row.price_current or row.price_open)
            )
            return OrderResult(
                status=status,
                filled_volume=min(volume or float(row.volume_initial), float(row.volume_initial)),
                avg_fill_price=price,
                position_id=str(row.position_id),
                **common,
            )
        if status is OrderStatus.REJECTED:
            return OrderResult(
                status=status,
                reject_reason=RejectReason.OTHER,
                message="rejected by the server",
                **common,
            )
        return OrderResult(status=status, **common)

    async def _closed_trade(self, deal: Any, known: Sequence[Any]) -> ClosedTrade:
        position_id = int(deal.position_id)
        entries = [d for d in known if int(d.position_id) == position_id and _is_entry(d)]
        if not entries:
            more = await self._call("history_deals_get", position=position_id) or ()
            entries = [d for d in more if _is_entry(d)]
        entry_volume = sum(float(d.volume) for d in entries)
        open_price = (
            sum(float(d.price) * float(d.volume) for d in entries) / entry_volume
            if entry_volume
            else float(deal.price)
        )
        share = float(deal.volume) / entry_volume if entry_volume else 0.0
        entry_commission = sum(float(d.commission) for d in entries) * share
        first = entries[0] if entries else deal
        opener = str(first.comment) if entries and int(first.reason) == _REASON_EXPERT else ""
        return ClosedTrade(
            position_id=str(position_id),
            symbol=deal.symbol,
            side=Side.SELL if int(deal.type) == _DEAL_BUY else Side.BUY,
            volume=float(deal.volume),
            open_price=open_price,
            close_price=float(deal.price),
            open_time=self._time(first),
            close_time=self._time(deal),
            profit=float(deal.profit),
            swap=float(deal.swap),
            commission=float(deal.commission) + float(getattr(deal, "fee", 0.0)) + entry_commission,
            reason=_DEAL_REASONS.get(int(deal.reason), CloseReason.OTHER),
            client_order_id=opener if _KEY.match(opener) else None,
        )

    def _position(self, row: Any) -> Position:
        comment = str(getattr(row, "comment", ""))
        return Position(
            position_id=str(row.ticket),
            symbol=row.symbol,
            side=Side.BUY if int(row.type) == _POSITION_BUY else Side.SELL,
            volume=float(row.volume),
            open_price=float(row.price_open),
            open_time=self._time(row),
            sl=float(row.sl) or None,
            tp=float(row.tp) or None,
            current_price=float(getattr(row, "price_current", 0.0)) or None,
            profit=float(getattr(row, "profit", 0.0)),
            swap=float(getattr(row, "swap", 0.0)),
            magic=int(getattr(row, "magic", 0)),
            client_order_id=comment if _KEY.match(comment) else None,
        )

    def _order_type(self, order: OrderRequest) -> int:
        mt5 = self._require()
        name = {
            OrderType.MARKET: "",
            OrderType.LIMIT: "_LIMIT",
            OrderType.STOP: "_STOP",
        }[order.type]
        return int(getattr(mt5, f"ORDER_TYPE_{order.side.value}{name}"))

    def _filling_types(self, info: Any) -> list[int]:
        """Allowed ORDER_FILLING_* values, most preferred first.

        ``filling_mode`` is a bitmask (SYMBOL_FILLING_FOK = 1, IOC = 2); an order's
        ``type_filling`` is an enum (FOK = 0, IOC = 1, RETURN = 2).
        """
        mt5 = self._require()
        flags = int(getattr(info, "filling_mode", 0) or 0)
        allowed = []
        if flags & int(mt5.SYMBOL_FILLING_IOC):
            allowed.append(int(mt5.ORDER_FILLING_IOC))
        if flags & int(mt5.SYMBOL_FILLING_FOK):
            allowed.append(int(mt5.ORDER_FILLING_FOK))
        if not allowed:
            allowed.append(int(mt5.ORDER_FILLING_RETURN))
        return allowed

    def _time(self, row: Any, field: str = "time") -> datetime:
        millis = int(getattr(row, f"{field}_msc", 0) or 0)
        seconds = millis / 1000 if millis else float(getattr(row, field, 0) or 0)
        return datetime.fromtimestamp(seconds, UTC) - self._offset

    def _remember(self, result: OrderResult) -> OrderResult:
        self._results[result.client_order_id] = result
        return result

    def _publish(self, event: BrokerEvent) -> None:
        self._hub.publish(event.model_copy(update={"broker": self._name}))


def _rejected(
    key: str,
    symbol: str,
    side: Side,
    volume: float,
    reason: RejectReason,
    message: str,
    time: datetime,
    code: str | None = None,
) -> OrderResult:
    return OrderResult(
        client_order_id=key,
        status=OrderStatus.REJECTED,
        symbol=symbol,
        side=side,
        requested_volume=volume,
        time=time,
        reject_reason=reason,
        broker_code=code,
        message=message,
    )


def _is_trade(deal: Any) -> bool:
    return int(deal.type) in (_DEAL_BUY, _DEAL_SELL)


def _is_entry(deal: Any) -> bool:
    return _is_trade(deal) and int(deal.entry) == _ENTRY_IN


def _is_exit(deal: Any) -> bool:
    return _is_trade(deal) and int(deal.entry) in (_ENTRY_OUT, _ENTRY_OUT_BY)


def _key_or(comment: Any, fallback: str) -> str:
    text = str(comment or "")
    return text if _KEY.match(text) else fallback
