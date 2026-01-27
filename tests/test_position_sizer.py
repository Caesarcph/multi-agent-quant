"""Tests for position sizing."""
from __future__ import annotations

from risk.position_sizer import calculate_volume


def test_calculate_volume_basic() -> None:
    signal = {
        "risk": {"risk_fraction_of_initial": 0.01, "stop_loss_pips": 20}
    }
    symbol_info = {
        "trade_tick_size": 0.00001,
        "trade_tick_value": 1.0,
        "digits": 5,
        "point": 0.00001,
    }
    account_info = {"balance": 10000.0}
    volume, notes = calculate_volume(signal, symbol_info, account_info, 0.01)
    assert notes == []
    assert abs(volume - 0.5) < 1e-6


def test_calculate_volume_missing_risk() -> None:
    signal = {}
    symbol_info = {"trade_tick_size": 0.00001, "trade_tick_value": 1.0}
    account_info = {"balance": 10000.0}
    volume, notes = calculate_volume(signal, symbol_info, account_info, 0.02)
    assert volume == 0.02
    assert "missing_risk_for_sizing" in notes
