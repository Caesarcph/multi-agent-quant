"""Position sizing utilities."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _pip_size(symbol_info: Dict[str, Any]) -> float:
    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    return point * 10 if digits in (3, 5) else point


def _stop_pips_from_price(
    signal: Dict[str, Any],
    risk: Dict[str, Any],
    symbol_info: Dict[str, Any],
    entry_price: float | None,
) -> float:
    """Stop distance in pips when the signal gives an absolute SL price instead of pips."""
    raw_sl = signal.get("sl_price", risk.get("stop_loss_price"))
    try:
        sl_price = float(raw_sl) if raw_sl is not None else 0.0
        entry = float(entry_price) if entry_price is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if sl_price <= 0.0 or entry <= 0.0:
        return 0.0
    return abs(entry - sl_price) / _pip_size(symbol_info)


def calculate_volume(
    signal: Dict[str, Any],
    symbol_info: Dict[str, Any],
    account_info: Dict[str, Any],
    default_volume: float,
    entry_price: float | None = None,
) -> Tuple[float, List[str]]:
    """Size a position so that hitting the stop loses ``risk_fraction_of_initial`` of balance.

    The stop distance comes from ``risk.stop_loss_pips``; if that is missing or zero it is
    derived from an absolute ``sl_price`` and ``entry_price``. Falls back to ``default_volume``
    (with a note) when the inputs are insufficient.
    """
    notes: List[str] = []
    risk = signal.get("risk")
    if not isinstance(risk, dict):
        return default_volume, ["missing_risk_for_sizing"]

    risk_fraction = float(risk.get("risk_fraction_of_initial") or 0.0)
    stop_loss_pips = float(risk.get("stop_loss_pips") or 0.0)
    if stop_loss_pips <= 0.0:
        stop_loss_pips = _stop_pips_from_price(signal, risk, symbol_info, entry_price)
        if stop_loss_pips > 0.0:
            notes.append("stop_distance_from_sl_price")
    if risk_fraction <= 0.0 or stop_loss_pips <= 0.0:
        return default_volume, ["invalid_risk_or_stop_loss"]

    balance = account_info.get("balance")
    equity = account_info.get("equity")
    basis = balance if balance is not None else equity
    if basis is None:
        return default_volume, ["missing_account_balance"]

    tick_size = float(symbol_info.get("trade_tick_size", 0.0))
    tick_value = float(symbol_info.get("trade_tick_value", 0.0))
    if tick_size <= 0.0 or tick_value <= 0.0:
        notes.append("missing_tick_value")
        return default_volume, notes

    pip_size = _pip_size(symbol_info)
    pip_value = tick_value * (pip_size / tick_size)
    if pip_value <= 0.0:
        notes.append("invalid_pip_value")
        return default_volume, notes

    risk_amount = float(basis) * risk_fraction
    volume = risk_amount / (stop_loss_pips * pip_value)
    if volume <= 0.0:
        notes.append("computed_volume_non_positive")
        return default_volume, notes

    return volume, notes


def size_order(
    signal: Dict[str, Any],
    symbol_info: Dict[str, Any],
    account_info: Dict[str, Any],
    default_volume: float,
    entry_price: float | None = None,
) -> Tuple[float, List[str]]:
    """Risk-based size, where a volume proposed by the signal (an LLM) may only shrink it."""
    volume, notes = calculate_volume(
        signal, symbol_info, account_info, default_volume, entry_price=entry_price
    )
    try:
        requested = float(signal.get("volume") or 0.0)
    except (TypeError, ValueError):
        requested = 0.0
    if 0.0 < requested < volume:
        volume = requested
        notes.append("capped_by_requested_volume")
    return volume, notes
