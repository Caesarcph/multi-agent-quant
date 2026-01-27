"""Order validation for execution."""
from __future__ import annotations

from typing import Any, Dict, List


def validate_signal_for_execution(signal: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    action = signal.get("action")
    if action not in {"BUY", "SELL", "HOLD"}:
        errors.append("invalid_action")
        return errors

    if action == "HOLD":
        return errors

    entry = signal.get("entry")
    if not isinstance(entry, dict):
        errors.append("missing_entry")
        return errors

    entry_type = entry.get("type")
    if entry_type not in {"market", "limit"}:
        errors.append("invalid_entry_type")

    if entry_type == "limit" and entry.get("price") is None:
        errors.append("missing_limit_price")

    volume = signal.get("volume")
    if volume is None or float(volume) <= 0.0:
        errors.append("invalid_volume")

    risk = signal.get("risk")
    if not isinstance(risk, dict):
        errors.append("missing_risk")
        return errors

    stop_loss_pips = float(risk.get("stop_loss_pips", 0.0))
    take_profit_pips = float(risk.get("take_profit_pips", 0.0))

    def _has_valid_price(value: Any) -> bool:
        try:
            return value is not None and float(value) > 0.0
        except (TypeError, ValueError):
            return False

    sl_price = signal.get("sl_price", risk.get("stop_loss_price"))
    tp_price = signal.get("tp_price", risk.get("take_profit_price"))

    if stop_loss_pips <= 0.0 and not _has_valid_price(sl_price):
        errors.append("invalid_stop_loss")
    if take_profit_pips <= 0.0 and not _has_valid_price(tp_price):
        errors.append("invalid_take_profit")

    return errors
