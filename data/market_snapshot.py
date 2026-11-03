"""Market snapshot assembly from MT5."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tools import indicators, mt5_tools

RATE_FIELDS = ("time", "open", "high", "low", "close", "tick_volume")


def _trim_rates(rates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for rate in rates:
        entry: dict[str, Any] = {}
        for field in RATE_FIELDS:
            if field in rate:
                entry[field] = rate[field]
        if "time" in entry:
            ts = datetime.fromtimestamp(entry["time"], tz=UTC)
            entry["time"] = ts.isoformat().replace("+00:00", "Z")
        trimmed.append(entry)
    return trimmed


def _build_symbol_universe(
    symbol: str,
    universe: list[str] | None,
    universe_limit: int | None,
) -> list[str]:
    if universe:
        symbols = [str(item) for item in universe]
    else:
        symbols = [info.get("name") for info in mt5_tools.get_symbols()]
        symbols = [name for name in symbols if name]

    if symbol not in symbols:
        symbols.insert(0, symbol)

    if universe_limit is not None:
        symbols = symbols[: int(universe_limit)]
    return symbols


def build_snapshot(
    symbol: str,
    timeframe: str,
    bars: int,
    universe: list[str] | None = None,
    universe_limit: int | None = None,
    include_universe: bool = False,
    include_universe_ticks: bool = False,
    positions: list[dict[str, Any]] | None = None,
    orders: list[dict[str, Any]] | None = None,
    include_positions: bool = False,
    include_orders: bool = False,
    include_indicators: bool = False,
    indicator_config: dict[str, Any] | None = None,
) -> dict:
    account = mt5_tools.get_account_info()
    symbol_info = mt5_tools.get_symbol_info(symbol)
    rates = mt5_tools.get_rates(symbol, timeframe, count=bars)

    snapshot: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "timeframe": timeframe,
        "account": {
            "login": account.get("login"),
            "balance": account.get("balance"),
            "equity": account.get("equity"),
            "margin_free": account.get("margin_free"),
            "leverage": account.get("leverage"),
            "currency": account.get("currency"),
        },
        "symbol_info": {
            "name": symbol_info.get("name"),
            "digits": symbol_info.get("digits"),
            "point": symbol_info.get("point"),
            "spread": symbol_info.get("spread"),
            "trade_mode": symbol_info.get("trade_mode"),
            "volume_min": symbol_info.get("volume_min"),
            "volume_step": symbol_info.get("volume_step"),
            "volume_max": symbol_info.get("volume_max"),
        },
        "rates": _trim_rates(rates),
    }

    if include_positions:
        if positions is None:
            positions = mt5_tools.get_positions()
        snapshot["positions"] = positions

    if include_orders:
        if orders is None:
            orders = mt5_tools.get_orders()
        snapshot["orders"] = orders

    if include_indicators:
        snapshot["indicators"] = indicators.compute_indicators(rates, indicator_config)

    if include_universe:
        symbol_universe = _build_symbol_universe(symbol, universe, universe_limit)
        snapshot["symbol_universe"] = symbol_universe
        if include_universe_ticks:
            ticks: dict[str, Any] = {}
            for sym in symbol_universe:
                try:
                    tick = mt5_tools.get_symbol_tick(sym)
                except Exception:
                    continue
                ticks[sym] = {
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "last": tick.get("last"),
                }
            snapshot["symbol_ticks"] = ticks
    return snapshot
