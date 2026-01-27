"""Tests for exposure guard."""
from __future__ import annotations

from risk.exposure_guard import check_exposure_limits


def test_exposure_max_positions() -> None:
    signal = {"action": "BUY", "symbol": "EURUSD"}
    positions = [{"symbol": "EURUSD", "type": 0} for _ in range(3)]
    cfg = {"max_concurrent_positions": 3}
    notes = check_exposure_limits(signal, positions, cfg)
    assert "max_positions_reached" in notes


def test_exposure_per_symbol() -> None:
    signal = {"action": "BUY", "symbol": "EURUSD"}
    positions = [{"symbol": "EURUSD", "type": 0} for _ in range(2)]
    cfg = {"max_positions_per_symbol": 2}
    notes = check_exposure_limits(signal, positions, cfg)
    assert "max_symbol_positions_reached" in notes
