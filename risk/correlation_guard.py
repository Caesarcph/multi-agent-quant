"""Correlation guard for exposure management."""

from __future__ import annotations

from typing import Any


def check_correlation_guard(
    signal: dict[str, Any],
    correlations: dict[str, dict[str, float]],
    positions: list[dict[str, Any]],
    threshold: float,
) -> list[str]:
    notes: list[str] = []
    action = signal.get("action")
    if action not in {"BUY", "SELL"}:
        return notes

    symbol = signal.get("symbol")
    if not symbol:
        return ["missing_symbol"]

    symbol_map = correlations.get(symbol, {})
    if not symbol_map:
        return notes

    for pos in positions:
        pos_symbol = pos.get("symbol")
        if not pos_symbol or pos_symbol == symbol:
            continue
        corr = symbol_map.get(pos_symbol)
        if corr is None:
            continue
        if abs(float(corr)) >= threshold:
            notes.append(f"correlated_exposure:{pos_symbol}:{corr}")

    return notes
