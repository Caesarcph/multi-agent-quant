"""A simulated MT5 trade server behind the ``MetaTrader5`` API.

``FakeMT5`` records requests and returns scripted answers; that is enough for
the legacy executor tests. The ``Mt5Broker`` adapter needs a terminal that
actually keeps state, so this subclass behaves like a hedging account on a
market-execution server:

* ``order_send`` opens, closes (``position=``), modifies (``TRADE_ACTION_SLTP``),
  places (``TRADE_ACTION_PENDING``) and removes (``TRADE_ACTION_REMOVE``),
  validating volume grid, filling type, stops level, deviation and margin and
  answering with the real retcodes;
* every execution writes history orders and deals the way MT5 does (position
  identifier = ticket of the opening order, deal ``entry`` IN/OUT, ``reason``
  EXPERT for API orders, SL/TP for server-side closes);
* ``set_quote`` moves the market and lets the server trigger pending orders,
  stops and expiries.

It is written independently of ``PaperBroker``: when both pass the broker
contract suite, two separate implementations agree on the behaviour.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.fakes import mt5 as c
from tests.fakes.mt5 import FakeMT5, _Record

EPS = 1e-9


def _ts(when: datetime) -> tuple[int, int]:
    millis = int(when.timestamp() * 1000)
    return millis // 1000, millis


class SimulatedTerminal(FakeMT5):
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 11, 3, 12, tzinfo=UTC)
        super().__init__()

    def reset(self) -> None:
        super().reset()
        self.account = {
            "login": 5001,
            "server": "Sim-Demo",
            "trade_mode": c.ACCOUNT_TRADE_MODE_DEMO,
            "currency": "USD",
            "leverage": 100,
            "balance": 10_000.0,
            "equity": 10_000.0,
            "margin": 0.0,
            "margin_free": 10_000.0,
            "trade_allowed": True,
            "trade_expert": True,
        }
        self.history_orders: list[dict[str, Any]] = []
        self._tickets = itertools.count(50_001)
        self.order_check_override: dict[str, Any] | None = None

    # ------------------------------------------------------------------ setup
    def add_symbol(self, name: str, **overrides: Any) -> dict[str, Any]:
        defaults = {
            "trade_stops_level": 0,
            "trade_freeze_level": 0,
            "currency_base": name[:3],
            "currency_profit": name[3:6],
            "currency_margin": name[:3],
            "trade_mode": c.SYMBOL_TRADE_MODE_FULL,
            "bid": 1.10000,
            "ask": 1.10010,
        }
        return super().add_symbol(name, **(defaults | overrides))

    def set_quote(self, symbol: str, bid: float, ask: float) -> None:
        self.symbols[symbol].update(bid=bid, ask=ask)
        self._process()

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)
        self._process()

    # ------------------------------------------------------------------ queries
    def symbol_info_tick(self, symbol: str) -> _Record | None:
        info = self.symbols.get(symbol)
        if info is None:
            return None
        seconds, millis = _ts(self.now)
        return _Record(
            time=seconds, time_msc=millis, bid=info["bid"], ask=info["ask"], last=0.0,
            volume=0, flags=0,
        )  # fmt: skip

    def positions_get(self, **filters: Any) -> tuple[_Record, ...]:
        self._mark_to_market()
        rows = [p for p in self.positions if _matches(p, filters, ticket_key="ticket")]
        return tuple(_Record(**p) for p in rows)

    def orders_get(self, **filters: Any) -> tuple[_Record, ...]:
        rows = [o for o in self.orders if _matches(o, filters, ticket_key="ticket")]
        return tuple(_Record(**o) for o in rows)

    def history_orders_get(
        self, date_from: datetime | None = None, date_to: datetime | None = None, **filters: Any
    ) -> tuple[_Record, ...]:
        rows = [
            o
            for o in self.history_orders
            if _in_window(o["time_setup"], date_from, date_to)
            and _matches(o, filters, ticket_key="ticket", position_key="position_id")
        ]
        return tuple(_Record(**o) for o in rows)

    def history_deals_get(
        self, date_from: datetime | None = None, date_to: datetime | None = None, **filters: Any
    ) -> tuple[_Record, ...]:
        rows = [
            d
            for d in self.deals
            if _in_window(d["time"], date_from, date_to)
            and _matches(d, filters, ticket_key="ticket", position_key="position_id")
        ]
        return tuple(_Record(**d) for d in rows)

    def account_info(self) -> _Record | None:
        self._mark_to_market()
        return super().account_info()

    def order_check(self, request: dict[str, Any]) -> _Record:
        if self.order_check_override is not None:
            return _Record(**self.order_check_override)
        return _Record(retcode=0, comment="Done", request=request)

    # ------------------------------------------------------------------ trading
    def order_send(self, request: dict[str, Any]) -> Any:
        self.sent_requests.append(dict(request))
        if self.order_send_results:
            scripted = self.order_send_results.popleft()
            return scripted(request) if callable(scripted) else scripted
        action = request.get("action")
        if action == c.TRADE_ACTION_DEAL:
            if request.get("position"):
                return self._close(request)
            return self._open(request)
        if action == c.TRADE_ACTION_PENDING:
            return self._place(request)
        if action == c.TRADE_ACTION_SLTP:
            return self._modify(request)
        if action == c.TRADE_ACTION_REMOVE:
            return self._remove(request)
        return self.result(c.TRADE_RETCODE_INVALID, comment="Invalid request")

    def _open(self, request: dict[str, Any]) -> Any:
        info = self.symbols.get(request.get("symbol", ""))
        if info is None:
            return self.result(c.TRADE_RETCODE_INVALID, comment="Invalid request")
        problem = self._common_checks(info, request)
        if problem is not None:
            return problem
        buy = request["type"] == c.ORDER_TYPE_BUY
        price = info["ask"] if buy else info["bid"]
        wanted = float(request.get("price") or 0.0)
        if wanted and abs(wanted - price) > int(request.get("deviation", 0)) * info["point"] + EPS:
            return self.result(c.TRADE_RETCODE_REQUOTE, bid=info["bid"], ask=info["ask"])
        exit_price = info["bid"] if buy else info["ask"]
        if not self._stops_ok(info, buy, exit_price, request):
            return self.result(c.TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        if not self._margin_ok(info, float(request["volume"]), price):
            return self.result(c.TRADE_RETCODE_NO_MONEY, comment="No money")
        order, deal = self._fill(info, request, buy, price)
        return self.result(
            c.TRADE_RETCODE_DONE, deal=deal, order=order, volume=float(request["volume"]),
            price=price, bid=info["bid"], ask=info["ask"], comment="Request executed",
        )  # fmt: skip

    def _close(self, request: dict[str, Any]) -> Any:
        position = self._position(int(request["position"]))
        if position is None:
            return self.result(c.TRADE_RETCODE_POSITION_CLOSED, comment="Position closed")
        info = self.symbols[position["symbol"]]
        volume = float(request["volume"])
        remaining = position["volume"] - volume
        closing_type = (
            c.ORDER_TYPE_SELL if position["type"] == c.POSITION_TYPE_BUY else c.ORDER_TYPE_BUY
        )
        if request.get("type") != closing_type:
            return self.result(c.TRADE_RETCODE_INVALID, comment="Invalid request")
        if (
            not _on_grid(info, volume)
            or remaining < -EPS
            or EPS < remaining < info["volume_min"] - EPS
        ):
            return self.result(c.TRADE_RETCODE_INVALID_VOLUME, comment="Invalid volume")
        if not self._filling_ok(info, request):
            return self.result(c.TRADE_RETCODE_INVALID_FILL, comment="Unsupported filling mode")
        price = info["bid"] if position["type"] == c.POSITION_TYPE_BUY else info["ask"]
        order, deal = self._exit(
            position, volume, price, c.DEAL_REASON_EXPERT, request.get("comment", "")
        )
        return self.result(
            c.TRADE_RETCODE_DONE, deal=deal, order=order, volume=volume, price=price,
            bid=info["bid"], ask=info["ask"], comment="Request executed",
        )  # fmt: skip

    def _modify(self, request: dict[str, Any]) -> Any:
        position = self._position(int(request.get("position", 0)))
        if position is None:
            return self.result(c.TRADE_RETCODE_POSITION_CLOSED, comment="Position closed")
        info = self.symbols[position["symbol"]]
        buy = position["type"] == c.POSITION_TYPE_BUY
        if not self._stops_ok(info, buy, info["bid"] if buy else info["ask"], request):
            return self.result(c.TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        position["sl"] = float(request.get("sl") or 0.0)
        position["tp"] = float(request.get("tp") or 0.0)
        return self.result(c.TRADE_RETCODE_DONE, comment="Request executed")

    def _place(self, request: dict[str, Any]) -> Any:
        info = self.symbols.get(request.get("symbol", ""))
        if info is None:
            return self.result(c.TRADE_RETCODE_INVALID, comment="Invalid request")
        problem = self._common_checks(info, request, check_filling=False)
        if problem is not None:
            return problem
        order_type = request["type"]
        buy = order_type in (c.ORDER_TYPE_BUY_LIMIT, c.ORDER_TYPE_BUY_STOP)
        price = float(request.get("price") or 0.0)
        market = info["ask"] if buy else info["bid"]
        limit = order_type in (c.ORDER_TYPE_BUY_LIMIT, c.ORDER_TYPE_SELL_LIMIT)
        distance = (market - price) if limit else (price - market)
        if not buy:
            distance = -distance
        if (
            price <= 0
            or distance <= EPS
            or distance < info["trade_stops_level"] * info["point"] - EPS
        ):
            return self.result(c.TRADE_RETCODE_INVALID_PRICE, comment="Invalid price")
        if not self._stops_ok(info, buy, price, request):
            return self.result(c.TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        expiration = 0
        if request.get("type_time") == c.ORDER_TIME_SPECIFIED:
            expiration = int(request.get("expiration") or 0)
            if expiration <= _ts(self.now)[0]:
                return self.result(c.TRADE_RETCODE_INVALID_EXPIRATION, comment="Invalid expiration")
        ticket = next(self._tickets)
        seconds, millis = _ts(self.now)
        self.orders.append(
            {
                "ticket": ticket, "time_setup": seconds, "time_setup_msc": millis,
                "time_expiration": expiration, "type": order_type,
                "type_time": request.get("type_time", c.ORDER_TIME_GTC),
                "state": c.ORDER_STATE_PLACED, "volume_initial": float(request["volume"]),
                "volume_current": float(request["volume"]), "price_open": price,
                "sl": float(request.get("sl") or 0.0), "tp": float(request.get("tp") or 0.0),
                "price_current": market, "symbol": info["name"],
                "comment": request.get("comment", ""), "magic": int(request.get("magic", 0)),
                "position_id": 0,
            }
        )  # fmt: skip
        return self.result(c.TRADE_RETCODE_PLACED, order=ticket, volume=float(request["volume"]))

    def _remove(self, request: dict[str, Any]) -> Any:
        ticket = int(request.get("order", 0))
        for order in self.orders:
            if order["ticket"] == ticket:
                self.orders.remove(order)
                self._archive(order, c.ORDER_STATE_CANCELED)
                return self.result(c.TRADE_RETCODE_DONE, order=ticket)
        return self.result(c.TRADE_RETCODE_INVALID, comment="Invalid request")

    # ------------------------------------------------------------------ server-side
    def _process(self) -> None:
        now_seconds = _ts(self.now)[0]
        for order in list(self.orders):
            if order["time_expiration"] and order["time_expiration"] <= now_seconds:
                self.orders.remove(order)
                self._archive(order, c.ORDER_STATE_EXPIRED)
                continue
            info = self.symbols[order["symbol"]]
            buy = order["type"] in (c.ORDER_TYPE_BUY_LIMIT, c.ORDER_TYPE_BUY_STOP)
            market = info["ask"] if buy else info["bid"]
            if order["type"] in (c.ORDER_TYPE_BUY_LIMIT, c.ORDER_TYPE_SELL_STOP):
                hit = market <= order["price_open"] + EPS
            else:
                hit = market >= order["price_open"] - EPS
            if hit:
                self.orders.remove(order)
                request = {
                    "symbol": order["symbol"], "volume": order["volume_initial"],
                    "type": c.ORDER_TYPE_BUY if buy else c.ORDER_TYPE_SELL,
                    "sl": order["sl"], "tp": order["tp"], "magic": order["magic"],
                    "comment": order["comment"],
                }  # fmt: skip
                self._fill(info, request, buy, market, ticket=order["ticket"], setup=order)
        for position in list(self.positions):
            info = self.symbols[position["symbol"]]
            buy = position["type"] == c.POSITION_TYPE_BUY
            exit_price = info["bid"] if buy else info["ask"]
            sl, tp = position["sl"], position["tp"]
            sign = 1 if buy else -1
            if sl and sign * (exit_price - sl) <= EPS:
                self._exit(position, position["volume"], exit_price, c.DEAL_REASON_SL, f"[sl {sl}]")
            elif tp and sign * (exit_price - tp) >= -EPS:
                self._exit(position, position["volume"], tp, c.DEAL_REASON_TP, f"[tp {tp}]")

    def _fill(
        self,
        info: dict[str, Any],
        request: dict[str, Any],
        buy: bool,
        price: float,
        *,
        ticket: int | None = None,
        setup: dict[str, Any] | None = None,
    ) -> tuple[int, int]:
        order_ticket = ticket if ticket is not None else next(self._tickets)
        deal_ticket = next(self._tickets)
        seconds, millis = _ts(self.now)
        volume = float(request["volume"])
        comment = request.get("comment", "")
        magic = int(request.get("magic", 0))
        self.positions.append(
            {
                "ticket": order_ticket, "identifier": order_ticket, "time": seconds,
                "time_msc": millis, "type": c.POSITION_TYPE_BUY if buy else c.POSITION_TYPE_SELL,
                "magic": magic, "volume": volume, "price_open": price,
                "sl": float(request.get("sl") or 0.0), "tp": float(request.get("tp") or 0.0),
                "price_current": price, "swap": 0.0, "profit": 0.0, "symbol": info["name"],
                "comment": comment,
            }
        )  # fmt: skip
        self.deals.append(
            {
                "ticket": deal_ticket, "order": order_ticket, "time": seconds, "time_msc": millis,
                "type": c.DEAL_TYPE_BUY if buy else c.DEAL_TYPE_SELL, "entry": c.DEAL_ENTRY_IN,
                "magic": magic, "position_id": order_ticket, "reason": c.DEAL_REASON_EXPERT,
                "volume": volume, "price": price, "commission": 0.0, "swap": 0.0,
                "profit": 0.0, "fee": 0.0, "symbol": info["name"], "comment": comment,
            }
        )  # fmt: skip
        history = dict(setup) if setup else {
            "ticket": order_ticket, "time_setup": seconds, "time_setup_msc": millis,
            "time_expiration": 0, "type": c.ORDER_TYPE_BUY if buy else c.ORDER_TYPE_SELL,
            "type_time": c.ORDER_TIME_GTC, "volume_initial": volume, "price_open": price,
            "sl": float(request.get("sl") or 0.0), "tp": float(request.get("tp") or 0.0),
            "symbol": info["name"], "comment": comment, "magic": magic,
        }  # fmt: skip
        self._archive(history, c.ORDER_STATE_FILLED, position_id=order_ticket, price=price)
        return order_ticket, deal_ticket

    def _exit(
        self, position: dict[str, Any], volume: float, price: float, reason: int, comment: str
    ) -> tuple[int, int]:
        info = self.symbols[position["symbol"]]
        buy = position["type"] == c.POSITION_TYPE_BUY
        sign = 1 if buy else -1
        profit = round(
            sign * (price - position["price_open"]) / info["trade_tick_size"]
            * info["trade_tick_value"] * volume,
            2,
        )  # fmt: skip
        self.account["balance"] = round(self.account["balance"] + profit, 2)
        position["volume"] = round(position["volume"] - volume, 8)
        if position["volume"] <= EPS:
            self.positions.remove(position)
        order_ticket, deal_ticket = next(self._tickets), next(self._tickets)
        seconds, millis = _ts(self.now)
        deal_type = c.DEAL_TYPE_SELL if buy else c.DEAL_TYPE_BUY
        self.deals.append(
            {
                "ticket": deal_ticket, "order": order_ticket, "time": seconds, "time_msc": millis,
                "type": deal_type, "entry": c.DEAL_ENTRY_OUT, "magic": position["magic"],
                "position_id": position["identifier"], "reason": reason, "volume": volume,
                "price": price, "commission": 0.0, "swap": 0.0, "profit": profit, "fee": 0.0,
                "symbol": position["symbol"], "comment": comment,
            }
        )  # fmt: skip
        self._archive(
            {
                "ticket": order_ticket, "time_setup": seconds, "time_setup_msc": millis,
                "time_expiration": 0, "type": deal_type, "type_time": c.ORDER_TIME_GTC,
                "volume_initial": volume, "price_open": price, "sl": 0.0, "tp": 0.0,
                "symbol": position["symbol"], "comment": comment, "magic": position["magic"],
            },
            c.ORDER_STATE_FILLED,
            position_id=position["identifier"],
            price=price,
        )  # fmt: skip
        self._mark_to_market()
        return order_ticket, deal_ticket

    def _archive(
        self, order: dict[str, Any], state: int, *, position_id: int = 0, price: float = 0.0
    ) -> None:
        seconds, millis = _ts(self.now)
        record = dict(order)
        record.update(
            state=state, time_done=seconds, time_done_msc=millis, position_id=position_id,
            volume_current=0.0, price_current=price or record.get("price_open", 0.0),
        )  # fmt: skip
        self.history_orders.append(record)

    # ------------------------------------------------------------------ checks
    def _common_checks(
        self, info: dict[str, Any], request: dict[str, Any], *, check_filling: bool = True
    ) -> Any:
        if info["trade_mode"] != c.SYMBOL_TRADE_MODE_FULL:
            return self.result(c.TRADE_RETCODE_TRADE_DISABLED, comment="Trade disabled")
        if not _on_grid(info, float(request.get("volume", 0.0))):
            return self.result(c.TRADE_RETCODE_INVALID_VOLUME, comment="Invalid volume")
        if check_filling and not self._filling_ok(info, request):
            return self.result(c.TRADE_RETCODE_INVALID_FILL, comment="Unsupported filling mode")
        return None

    @staticmethod
    def _filling_ok(info: dict[str, Any], request: dict[str, Any]) -> bool:
        flags = int(info.get("filling_mode", 0))
        allowed = set()
        if flags & c.SYMBOL_FILLING_FOK:
            allowed.add(c.ORDER_FILLING_FOK)
        if flags & c.SYMBOL_FILLING_IOC:
            allowed.add(c.ORDER_FILLING_IOC)
        if not allowed:
            allowed.add(c.ORDER_FILLING_RETURN)
        return request.get("type_filling", c.ORDER_FILLING_FOK) in allowed

    @staticmethod
    def _stops_ok(
        info: dict[str, Any], buy: bool, reference: float, request: dict[str, Any]
    ) -> bool:
        minimum = info["trade_stops_level"] * info["point"]
        sign = 1 if buy else -1
        sl, tp = float(request.get("sl") or 0.0), float(request.get("tp") or 0.0)
        if sl and (sign * (reference - sl) <= EPS or sign * (reference - sl) < minimum - EPS):
            return False
        return not (
            tp and (sign * (tp - reference) <= EPS or sign * (tp - reference) < minimum - EPS)
        )

    def _margin_ok(self, info: dict[str, Any], volume: float, price: float) -> bool:
        required = self.order_calc_margin(0, info["name"], volume, price) or 0.0
        self._mark_to_market()
        return required <= self.account["margin_free"] + EPS

    def _position(self, ticket: int) -> dict[str, Any] | None:
        return next((p for p in self.positions if p["ticket"] == ticket), None)

    def _mark_to_market(self) -> None:
        if self.account is None:  # a test simulating a lost terminal
            return
        floating, margin = 0.0, 0.0
        for position in self.positions:
            info = self.symbols[position["symbol"]]
            buy = position["type"] == c.POSITION_TYPE_BUY
            current = info["bid"] if buy else info["ask"]
            sign = 1 if buy else -1
            position["price_current"] = current
            position["profit"] = round(
                sign * (current - position["price_open"]) / info["trade_tick_size"]
                * info["trade_tick_value"] * position["volume"],
                2,
            )  # fmt: skip
            floating += position["profit"]
            margin += (
                self.order_calc_margin(0, position["symbol"], position["volume"], current) or 0.0
            )
        account = self.account
        account["equity"] = round(account["balance"] + floating, 2)
        account["margin"] = round(margin, 2)
        account["margin_free"] = round(account["equity"] - margin, 2)


def _on_grid(info: dict[str, Any], volume: float) -> bool:
    if volume < info["volume_min"] - EPS or volume > info["volume_max"] + EPS:
        return False
    steps = volume / info["volume_step"]
    return abs(steps - round(steps)) < 1e-6


def _in_window(seconds: int, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and seconds < int(start.timestamp()):
        return False
    return not (end is not None and seconds > int(end.timestamp()))


def _matches(
    row: dict[str, Any], filters: dict[str, Any], *, ticket_key: str, position_key: str = ""
) -> bool:
    if "symbol" in filters and row.get("symbol") != filters["symbol"]:
        return False
    if "ticket" in filters and row.get(ticket_key) != int(filters["ticket"]):
        return False
    return not (
        "position" in filters and position_key and row.get(position_key) != int(filters["position"])
    )
