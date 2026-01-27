"""Position sizing utilities."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def calculate_volume(
    signal: Dict[str, Any],
    symbol_info: Dict[str, Any],
    account_info: Dict[str, Any],
    default_volume: float,
) -> Tuple[float, List[str]]:
    notes: List[str] = []
    risk = signal.get("risk")
    if not isinstance(risk, dict):
        return default_volume, ["missing_risk_for_sizing"]

    risk_fraction = float(risk.get("risk_fraction_of_initial", 0.0))
    stop_loss_pips = float(risk.get("stop_loss_pips", 0.0))
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

    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    pip_size = point * 10 if digits in (3, 5) else point
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
