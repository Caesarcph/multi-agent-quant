"""Builders for test data shared across test modules."""

from __future__ import annotations

import copy
from typing import Any

_RESEARCH_SIGNAL: dict[str, Any] = {
    "model": "deepseek",
    "timestamp": "2026-01-21T00:00:00Z",
    "symbol": "EURUSD",
    "timeframe": "M15",
    "action": "BUY",
    "volume": 0.1,
    "confidence": 0.7,
    "horizon": "intra",
    "entry": {"type": "market", "price": None},
    "risk": {
        "stop_loss_pips": 20,
        "take_profit_pips": 40,
        "risk_fraction_of_initial": 0.01,
    },
    "analysis": {
        "basis": "technical",
        "time_horizon": "short_term",
        "setup": "Pullback to support in an uptrend.",
        "sl_tp_rationale": "SL below support, TP at prior high.",
    },
    "context": {
        "regime": "trending_up",
        "key_levels": {"support": [1.0830], "resistance": [1.0890]},
    },
    "reason_codes": ["SUPPORT_BOUNCE"],
    "invalidation": ["CLOSE_BELOW_1.0820"],
    "ttl_minutes": 60,
}


def research_signal(**overrides: Any) -> dict[str, Any]:
    """A research-agent signal that satisfies agents/prompts/output_schema.json."""
    signal = copy.deepcopy(_RESEARCH_SIGNAL)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(signal.get(key), dict):
            signal[key].update(value)
        else:
            signal[key] = value
    return signal
