"""In-memory stand-in for the ``MetaTrader5`` package.

The official package only exists on Windows and talks to a running terminal, so
tests use this fake instead. It is deliberately explicit: only the functions and
constants the project uses exist, and constant values match the real package, so
a typo in production code fails here instead of silently passing.

Typical use (see ``tests/conftest.py``)::

    def test_something(fake_mt5):
        fake_mt5.add_symbol("EURUSD", bid=1.0850, ask=1.0851)
        fake_mt5.order_send_results.append(fake_mt5.result(retcode=10009))
        ...
        assert fake_mt5.sent_requests[0]["volume"] == 0.1
"""

from __future__ import annotations

import types
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

import numpy as np

# --------------------------------------------------------------------------- constants
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769
TIMEFRAME_MN1 = 49153

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6

ORDER_TIME_GTC = 0

# Order filling *types* (what a request's type_filling expects)
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

# Symbol filling *flags* (bitmask in symbol_info().filling_mode)
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2

POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

COPY_TICKS_ALL = -1

TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_NO_MONEY = 10019

_CONSTANT_NAMES = [name for name in dir() if name.isupper()]


# --------------------------------------------------------------------------- records
class _Record(types.SimpleNamespace):
    """Mimics the named tuples returned by MetaTrader5 (attribute access + _asdict)."""

    def _asdict(self) -> dict[str, Any]:
        return dict(vars(self))


class OrderSendResult(NamedTuple):
    retcode: int
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0
    retcode_external: int = 0


def default_symbol(name: str, **overrides: Any) -> dict[str, Any]:
    """A 5-digit FX symbol (EURUSD-like) unless overridden."""
    info: dict[str, Any] = {
        "name": name,
        "visible": True,
        "trade_mode": 4,  # SYMBOL_TRADE_MODE_FULL
        "digits": 5,
        "point": 0.00001,
        "spread": 10,  # points
        "trade_tick_size": 0.00001,
        "trade_tick_value": 1.0,
        "trade_contract_size": 100000.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "filling_mode": SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC,
        "session_deals": 0,
        "session_volume": 0.0,
        "bid": 1.08500,
        "ask": 1.08510,
    }
    info.update(overrides)
    return info


# --------------------------------------------------------------------------- the fake
class FakeMT5(types.ModuleType):
    """A module object that can be installed as ``sys.modules["MetaTrader5"]``."""

    def __init__(self) -> None:
        super().__init__("MetaTrader5", "Fake MetaTrader5 package for tests")
        g = globals()
        for name in _CONSTANT_NAMES:
            setattr(self, name, g[name])
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.initialized = False
        self.init_kwargs: dict[str, Any] = {}
        self.error: tuple[int, str] = (1, "Success")
        self.account: dict[str, Any] | None = {
            "login": 123456,
            "balance": 10000.0,
            "equity": 10000.0,
            "margin": 0.0,
            "margin_free": 10000.0,
            "leverage": 100,
            "currency": "USD",
        }
        self.symbols: dict[str, dict[str, Any]] = {}
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.deals: list[dict[str, Any]] = []
        self.rates: dict[str, np.ndarray] = {}
        self.ticks: dict[str, np.ndarray] = {}
        self.sent_requests: list[dict[str, Any]] = []
        # Scripted responses for order_send: OrderSendResult, None, or a callable(request)
        self.order_send_results: deque[Any] = deque()
        self.default_order_result: Callable[[dict[str, Any]], Any] = lambda request: self.result(
            TRADE_RETCODE_DONE, volume=request.get("volume", 0.0)
        )

    def add_symbol(self, name: str, **overrides: Any) -> dict[str, Any]:
        info = default_symbol(name, **overrides)
        self.symbols[name] = info
        return info

    def add_position(
        self,
        symbol: str,
        type: int = POSITION_TYPE_BUY,
        volume: float = 0.1,
        price_open: float = 1.0850,
        ticket: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        position = {
            "ticket": ticket if ticket is not None else 1000 + len(self.positions),
            "symbol": symbol,
            "type": type,
            "volume": volume,
            "price_open": price_open,
            "sl": 0.0,
            "tp": 0.0,
            "profit": 0.0,
            "magic": 0,
            "comment": "",
        }
        position.update(extra)
        self.positions.append(position)
        return position

    @staticmethod
    def result(retcode: int = TRADE_RETCODE_DONE, **fields: Any) -> OrderSendResult:
        return OrderSendResult(retcode=retcode, **fields)

    def set_rates(self, symbol: str, closes: list[float], start: int = 1_700_000_000) -> None:
        dtype = [
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("spread", "i4"),
            ("real_volume", "i8"),
        ]
        rows = [
            (start + 60 * i, c, c + 0.0002, c - 0.0002, c, 100, 10, 0) for i, c in enumerate(closes)
        ]
        self.rates[symbol] = np.array(rows, dtype=dtype)

    # ------------------------------------------------------------------ lifecycle
    def initialize(self, path: str | None = None, **kwargs: Any) -> bool:
        self.initialized = True
        self.init_kwargs = {"path": path, **kwargs}
        return True

    def shutdown(self) -> None:
        self.initialized = False

    def last_error(self) -> tuple[int, str]:
        return self.error

    # ------------------------------------------------------------------ queries
    def account_info(self) -> _Record | None:
        return _Record(**self.account) if self.account is not None else None

    def symbols_get(self) -> tuple[_Record, ...]:
        return tuple(_Record(**info) for info in self.symbols.values())

    def symbol_info(self, symbol: str) -> _Record | None:
        info = self.symbols.get(symbol)
        return _Record(**info) if info is not None else None

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return symbol in self.symbols

    def symbol_info_tick(self, symbol: str) -> _Record | None:
        info = self.symbols.get(symbol)
        if info is None:
            return None
        return _Record(
            time=0, bid=info["bid"], ask=info["ask"], last=0.0, volume=0, time_msc=0, flags=0
        )

    def positions_get(self, **filters: Any) -> tuple[_Record, ...]:
        rows = self.positions
        if "symbol" in filters:
            rows = [p for p in rows if p["symbol"] == filters["symbol"]]
        return tuple(_Record(**p) for p in rows)

    def orders_get(self, **filters: Any) -> tuple[_Record, ...]:
        return tuple(_Record(**o) for o in self.orders)

    def history_deals_get(
        self, date_from: datetime, date_to: datetime, **filters: Any
    ) -> tuple[_Record, ...]:
        return tuple(_Record(**d) for d in self.deals)

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> np.ndarray | None:
        rates = self.rates.get(symbol)
        if rates is None:
            return None
        end = len(rates) - start_pos
        return rates[max(0, end - count) : end]

    def copy_rates_from(
        self, symbol: str, timeframe: int, date_from: datetime, count: int
    ) -> np.ndarray | None:
        rates = self.rates.get(symbol)
        return None if rates is None else rates[-count:]

    def copy_ticks_from(
        self, symbol: str, date_from: datetime, count: int, flags: int
    ) -> np.ndarray | None:
        ticks = self.ticks.get(symbol)
        return None if ticks is None else ticks[:count]

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float | None:
        info = self.symbols.get(symbol)
        if info is None:
            return None
        leverage = float(self.account["leverage"]) if self.account else 100.0
        return volume * float(info["trade_contract_size"]) * price / leverage

    # ------------------------------------------------------------------ trading
    def order_send(self, request: dict[str, Any]) -> Any:
        self.sent_requests.append(dict(request))
        if self.order_send_results:
            scripted = self.order_send_results.popleft()
            return scripted(request) if callable(scripted) else scripted
        return self.default_order_result(request)
