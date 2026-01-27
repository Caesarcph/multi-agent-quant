"""Tests for order validation."""
from __future__ import annotations

from execution.order_validator import validate_signal_for_execution


def test_validate_signal_hold_ok() -> None:
    signal = {"action": "HOLD"}
    assert validate_signal_for_execution(signal) == []


def test_validate_signal_limit_requires_price() -> None:
    signal = {
        "action": "BUY",
        "volume": 0.1,
        "entry": {"type": "limit", "price": None},
        "risk": {"stop_loss_pips": 10, "take_profit_pips": 20},
    }
    errors = validate_signal_for_execution(signal)
    assert "missing_limit_price" in errors


def test_validate_signal_accepts_price_based_stops() -> None:
    signal = {
        "action": "BUY",
        "volume": 0.1,
        "entry": {"type": "market", "price": None},
        "risk": {"stop_loss_pips": 0, "take_profit_pips": 0},
        "sl_price": 1.2345,
        "tp_price": 1.2365,
    }
    errors = validate_signal_for_execution(signal)
    assert errors == []
