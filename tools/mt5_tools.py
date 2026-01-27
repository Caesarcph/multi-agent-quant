"""MT5 data access helpers."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

import MetaTrader5 as mt5

from tools import indicators
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def _to_py(value: Any) -> Any:
    try:
        return value.item()
    except AttributeError:
        return value


def _rows_to_dicts(rows: Iterable[Any]) -> list[Dict[str, Any]]:
    if rows is None:
        return []
    names = rows.dtype.names
    if not names:
        return []
    output = []
    for row in rows:
        output.append({name: _to_py(row[name]) for name in names})
    return output


def resolve_timeframe(timeframe: int | str) -> int:
    if isinstance(timeframe, int):
        return timeframe
    key = timeframe.upper()
    if key not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAMES[key]


def get_account_info() -> dict:
    info = mt5.account_info()
    if info is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 account_info failed: {code} {message}")
    return info._asdict()


def get_symbols() -> list[dict]:
    symbols = mt5.symbols_get()
    if symbols is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 symbols_get failed: {code} {message}")
    return [symbol._asdict() for symbol in symbols]


def get_positions() -> list[dict]:
    positions = mt5.positions_get()
    if positions is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 positions_get failed: {code} {message}")
    return [position._asdict() for position in positions]


def get_orders() -> list[dict]:
    orders = mt5.orders_get()
    if orders is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 orders_get failed: {code} {message}")
    return [order._asdict() for order in orders]


def get_symbol_info(symbol: str, ensure_select: bool = True) -> dict:
    info = mt5.symbol_info(symbol)
    if info is None and ensure_select:
        if not mt5.symbol_select(symbol, True):
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 symbol_select failed: {code} {message}")
        info = mt5.symbol_info(symbol)
    if info is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 symbol_info failed: {code} {message}")
    return info._asdict()


def get_symbol_tick(symbol: str, ensure_select: bool = True) -> dict:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None and ensure_select:
        if not mt5.symbol_select(symbol, True):
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 symbol_select failed: {code} {message}")
        tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 symbol_info_tick failed: {code} {message}")
    return tick._asdict()


def get_rates(
    symbol: str,
    timeframe: int | str,
    count: int = 100,
    start_pos: int = 0,
    ensure_select: bool = True,
) -> list[Dict[str, Any]]:
    tf = resolve_timeframe(timeframe)
    if ensure_select:
        mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
    if rates is None or len(rates) == 0:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 copy_rates_from_pos failed: {code} {message}")
    return _rows_to_dicts(rates)


def get_rates_from(
    symbol: str,
    timeframe: int | str,
    from_datetime: datetime,
    count: int = 1,
) -> list[Dict[str, Any]]:
    tf = resolve_timeframe(timeframe)
    rates = mt5.copy_rates_from(symbol, tf, from_datetime, count)
    if rates is None or len(rates) == 0:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 copy_rates_from failed: {code} {message}")
    return _rows_to_dicts(rates)


def get_rate_at(
    symbol: str,
    timeframe: int | str,
    timestamp: datetime,
) -> Optional[Dict[str, Any]]:
    rates = get_rates_from(symbol, timeframe, timestamp, count=1)
    if not rates:
        return None
    return rates[0]


def get_ticks(
    symbol: str,
    count: int = 100,
    from_datetime: datetime | None = None,
    flags: int = mt5.COPY_TICKS_ALL,
) -> list[Dict[str, Any]]:
    if from_datetime is None:
        from_datetime = datetime.utcnow() - timedelta(days=1)
    ticks = mt5.copy_ticks_from(symbol, from_datetime, count, flags)
    if ticks is None or len(ticks) == 0:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 copy_ticks_from_pos failed: {code} {message}")
    return _rows_to_dicts(ticks)


def get_deals(
    start: datetime,
    end: datetime,
) -> list[Dict[str, Any]]:
    deals = mt5.history_deals_get(start, end)
    if deals is None:
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 history_deals_get failed: {code} {message}")
    return [deal._asdict() for deal in deals]


def calculate_indicator(
    indicator: str,
    rates: list[Dict[str, Any]],
    **kwargs: Any,
) -> Any:
    return indicators.calculate_indicator(indicator, rates, **kwargs)
