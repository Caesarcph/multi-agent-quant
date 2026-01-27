"""Tests for correlation guard."""
from __future__ import annotations

from risk.correlation_guard import check_correlation_guard


def test_correlation_guard_blocks() -> None:
    signal = {"action": "BUY", "symbol": "EURUSD"}
    correlations = {"EURUSD": {"GBPUSD": 0.91}}
    positions = [{"symbol": "GBPUSD", "type": 0}]
    notes = check_correlation_guard(signal, correlations, positions, threshold=0.8)
    assert "correlated_exposure:GBPUSD:0.91" in notes


def test_correlation_guard_no_positions() -> None:
    signal = {"action": "BUY", "symbol": "EURUSD"}
    correlations = {"EURUSD": {"GBPUSD": 0.91}}
    notes = check_correlation_guard(signal, correlations, [], threshold=0.8)
    assert notes == []
