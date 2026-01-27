"""Risk guard stubs for signal validation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


def apply_basic_risk(signal: Dict[str, Any], risk_config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    output = deepcopy(signal)
    notes: List[str] = []
    trade_cfg = risk_config.get("trade_level", {})

    require_sl = bool(trade_cfg.get("require_stop_loss", False))
    max_risk = trade_cfg.get("per_trade_risk")

    risk = output.get("risk")
    if risk is None:
        notes.append("missing_risk_block")
        output["action"] = "HOLD"
        return output, notes

    def _has_valid_price(value: Any) -> bool:
        try:
            return value is not None and float(value) > 0.0
        except (TypeError, ValueError):
            return False

    stop_loss_pips = float(risk.get("stop_loss_pips", 0.0))
    sl_price = output.get("sl_price", risk.get("stop_loss_price"))
    if require_sl and stop_loss_pips <= 0.0 and not _has_valid_price(sl_price):
        notes.append("missing_stop_loss")
        output["action"] = "HOLD"

    if max_risk is not None:
        max_risk_value = float(max_risk)
        risk_fraction = float(risk.get("risk_fraction_of_initial", 0.0))
        if risk_fraction > max_risk_value:
            risk["risk_fraction_of_initial"] = max_risk_value
            notes.append("risk_fraction_clamped")

    return output, notes
