"""Account-level risk controls."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


def _drawdown_multiplier(drawdown: float, throttles: Dict[str, Any]) -> float:
    multiplier = 1.0
    for raw_threshold, raw_value in throttles.items():
        try:
            threshold = float(raw_threshold)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if drawdown >= threshold:
            multiplier = value
    return multiplier


def apply_account_limits(
    signal: Dict[str, Any],
    account_state: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    output = deepcopy(signal)
    notes: List[str] = []
    if not account_state:
        return output, notes

    drawdown_from_initial = float(account_state.get("drawdown_from_initial", 0.0))
    max_drawdown = config.get("max_drawdown")
    if max_drawdown is not None and drawdown_from_initial >= float(max_drawdown):
        output["action"] = "HOLD"
        notes.append("max_drawdown_reached")
        return output, notes

    if account_state.get("capital_preservation"):
        output["action"] = "HOLD"
        notes.append("capital_preservation_mode")
        return output, notes

    throttles = config.get("drawdown_throttle", {}) or {}
    if throttles:
        multiplier = _drawdown_multiplier(drawdown_from_initial, throttles)
        if multiplier < 1.0:
            risk = output.get("risk")
            if isinstance(risk, dict):
                risk_fraction = float(risk.get("risk_fraction_of_initial", 0.0))
                risk["risk_fraction_of_initial"] = max(risk_fraction * multiplier, 0.0)
            notes.append(f"drawdown_throttle:{multiplier:.2f}")

    return output, notes
