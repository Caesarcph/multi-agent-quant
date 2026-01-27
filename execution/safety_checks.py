"""Execution safety checks."""
from __future__ import annotations

from typing import Any, Dict, List


def check_execution_safety(signal: Dict[str, Any], symbol_info: Dict[str, Any]) -> List[str]:
    notes: List[str] = []

    if signal.get("action") in ("BUY", "SELL"):
        trade_mode = int(symbol_info.get("trade_mode", 0))
        if trade_mode != 4:
            notes.append(f"symbol_not_tradeable:{trade_mode}")

    spread = symbol_info.get("spread")
    if spread is not None:
        max_spread = symbol_info.get("max_spread")
        if max_spread is not None and spread > max_spread:
            notes.append("spread_exceeds_symbol_limit")

    return notes


def check_liquidity(
    symbol_info: Dict[str, Any],
    tick: Dict[str, Any],
    config: Dict[str, Any],
) -> List[str]:
    notes: List[str] = []

    if config.get("require_tick_bid_ask", True):
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        if bid <= 0.0 or ask <= 0.0:
            notes.append("invalid_tick_bid_ask")

    min_deals = config.get("min_session_deals")
    if min_deals is not None:
        deals = float(symbol_info.get("session_deals", 0.0))
        if deals < float(min_deals):
            notes.append(f"session_deals_below:{deals}")

    min_volume = config.get("min_session_volume")
    if min_volume is not None:
        volume = float(symbol_info.get("session_volume", 0.0))
        if volume < float(min_volume):
            notes.append(f"session_volume_below:{volume}")

    return notes
