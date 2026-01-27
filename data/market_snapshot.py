"""Market snapshot assembly from MT5."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from tools import mt5_tools
from tools import indicators

RATE_FIELDS = ("time", "open", "high", "low", "close", "tick_volume")


def _trim_rates(rates: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    trimmed: list[Dict[str, Any]] = []
    for rate in rates:
        entry: Dict[str, Any] = {}
        for field in RATE_FIELDS:
            if field in rate:
                entry[field] = rate[field]
        if "time" in entry:
            ts = datetime.fromtimestamp(entry["time"], tz=timezone.utc)
            entry["time"] = ts.isoformat().replace("+00:00", "Z")
        trimmed.append(entry)
    return trimmed


def _build_symbol_universe(
    symbol: str,
    universe: Optional[List[str]],
    universe_limit: Optional[int],
) -> List[str]:
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
    universe: Optional[List[str]] = None,
    universe_limit: Optional[int] = None,
    include_universe: bool = False,
    include_universe_ticks: bool = False,
    positions: Optional[List[Dict[str, Any]]] = None,
    orders: Optional[List[Dict[str, Any]]] = None,
    include_positions: bool = False,
    include_orders: bool = False,
    include_indicators: bool = False,
    indicator_config: Optional[Dict[str, Any]] = None,
) -> dict:
    account = mt5_tools.get_account_info()
    symbol_info = mt5_tools.get_symbol_info(symbol)
    rates = mt5_tools.get_rates(symbol, timeframe, count=bars)

    snapshot: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
        snapshot["indicators"] = indicators.compute_indicators(
            rates, indicator_config
        )

    if include_universe:
        symbol_universe = _build_symbol_universe(symbol, universe, universe_limit)
        snapshot["symbol_universe"] = symbol_universe
        if include_universe_ticks:
            ticks: Dict[str, Any] = {}
            for sym in symbol_universe:
                try:
                    tick = mt5_tools.get_symbol_tick(sym)
                except Exception:  # noqa: BLE001
                    continue
                ticks[sym] = {
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "last": tick.get("last"),
                }
            snapshot["symbol_ticks"] = ticks
    return snapshot
