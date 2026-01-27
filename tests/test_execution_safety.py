"""Tests for execution safety checks."""
from __future__ import annotations

from execution.safety_checks import check_execution_safety


def test_safety_blocks_trade_mode() -> None:
    signal = {"action": "BUY"}
    symbol_info = {"trade_mode": 0, "spread": 10}
    notes = check_execution_safety(signal, symbol_info)
    assert "symbol_not_tradeable:0" in notes


def test_safety_allows_trade_mode() -> None:
    signal = {"action": "SELL"}
    symbol_info = {"trade_mode": 4, "spread": 10}
    notes = check_execution_safety(signal, symbol_info)
    assert notes == []
