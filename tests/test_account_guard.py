"""Tests for account-level guards."""
from __future__ import annotations

from risk.account_guard import apply_account_limits


def test_drawdown_throttle_scales_risk() -> None:
    signal = {"action": "BUY", "risk": {"risk_fraction_of_initial": 0.04}}
    account_state = {"drawdown_from_initial": 0.26}
    config = {"drawdown_throttle": {"0.10": 0.75, "0.25": 0.5}}
    updated, notes = apply_account_limits(signal, account_state, config)
    assert abs(updated["risk"]["risk_fraction_of_initial"] - 0.02) < 1e-6
    assert any(note.startswith("drawdown_throttle") for note in notes)


def test_capital_preservation_blocks_trades() -> None:
    signal = {"action": "BUY", "risk": {"risk_fraction_of_initial": 0.02}}
    account_state = {"capital_preservation": True, "drawdown_from_initial": 0.4}
    config = {"max_drawdown": 0.5}
    updated, notes = apply_account_limits(signal, account_state, config)
    assert updated["action"] == "HOLD"
    assert "capital_preservation_mode" in notes


def test_max_drawdown_blocks_trades() -> None:
    signal = {"action": "SELL", "risk": {"risk_fraction_of_initial": 0.02}}
    account_state = {"drawdown_from_initial": 0.6}
    config = {"max_drawdown": 0.5}
    updated, notes = apply_account_limits(signal, account_state, config)
    assert updated["action"] == "HOLD"
    assert "max_drawdown_reached" in notes
