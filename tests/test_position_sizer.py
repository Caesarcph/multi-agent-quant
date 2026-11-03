"""Tests for position sizing."""

from __future__ import annotations

from risk.position_sizer import calculate_volume, size_order


def test_calculate_volume_basic() -> None:
    signal = {"risk": {"risk_fraction_of_initial": 0.01, "stop_loss_pips": 20}}
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


EURUSD = {
    "trade_tick_size": 0.00001,
    "trade_tick_value": 1.0,
    "digits": 5,
    "point": 0.00001,
}


def test_calculate_volume_from_sl_price() -> None:
    # The trading department gives SL as a price and stop_loss_pips = 0.
    signal = {
        "sl_price": 1.0800,
        "risk": {"risk_fraction_of_initial": 0.01, "stop_loss_pips": 0},
    }
    volume, notes = calculate_volume(signal, EURUSD, {"balance": 10000.0}, 0.01, entry_price=1.0820)
    # 20 pips stop, $10/pip per lot, $100 risk -> 0.5 lots
    assert abs(volume - 0.5) < 1e-6
    assert notes == ["stop_distance_from_sl_price"]


def test_calculate_volume_sl_price_without_entry_falls_back() -> None:
    signal = {"sl_price": 1.0800, "risk": {"risk_fraction_of_initial": 0.01}}
    volume, notes = calculate_volume(signal, EURUSD, {"balance": 10000.0}, 0.01)
    assert volume == 0.01
    assert "invalid_risk_or_stop_loss" in notes


def test_size_order_llm_volume_can_only_shrink() -> None:
    base = {"risk": {"risk_fraction_of_initial": 0.01, "stop_loss_pips": 20}}

    bigger, notes = size_order({**base, "volume": 10.0}, EURUSD, {"balance": 10000.0}, 0.01)
    assert abs(bigger - 0.5) < 1e-6  # asking for 10 lots does not raise the risk-based 0.5
    assert "capped_by_requested_volume" not in notes

    smaller, notes = size_order({**base, "volume": 0.2}, EURUSD, {"balance": 10000.0}, 0.01)
    assert smaller == 0.2
    assert "capped_by_requested_volume" in notes

    ignored, _ = size_order({**base, "volume": "lots"}, EURUSD, {"balance": 10000.0}, 0.01)
    assert abs(ignored - 0.5) < 1e-6
