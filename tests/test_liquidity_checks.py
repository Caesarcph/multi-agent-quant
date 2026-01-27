"""Tests for liquidity checks."""
from __future__ import annotations

from execution.safety_checks import check_liquidity


def test_liquidity_requires_bid_ask() -> None:
    symbol_info = {"session_deals": 10, "session_volume": 100}
    tick = {"bid": 0.0, "ask": 1.0}
    cfg = {"require_tick_bid_ask": True}
    notes = check_liquidity(symbol_info, tick, cfg)
    assert "invalid_tick_bid_ask" in notes


def test_liquidity_session_thresholds() -> None:
    symbol_info = {"session_deals": 2, "session_volume": 50}
    tick = {"bid": 1.0, "ask": 1.1}
    cfg = {"min_session_deals": 5, "min_session_volume": 100}
    notes = check_liquidity(symbol_info, tick, cfg)
    assert "session_deals_below:2.0" in notes
    assert "session_volume_below:50.0" in notes
