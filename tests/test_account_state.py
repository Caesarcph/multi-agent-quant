"""Tests for account state tracking."""
from __future__ import annotations

from data.account_state import update_account_state


def test_account_state_tracks_drawdown(tmp_path) -> None:
    path = tmp_path / "account_state.json"
    config = {
        "capital_preservation": {
            "enabled": True,
            "trigger_drawdown": 0.2,
            "resume_drawdown": 0.1,
        }
    }

    state = update_account_state(
        path,
        {"balance": 1000.0, "equity": 1000.0},
        config,
    )
    assert state["initial_balance"] == 1000.0
    assert state["drawdown_from_initial"] == 0.0
    assert not state.get("capital_preservation", False)

    state = update_account_state(
        path,
        {"balance": 1000.0, "equity": 800.0},
        config,
    )
    assert abs(state["drawdown_from_initial"] - 0.2) < 1e-6
    assert state.get("capital_preservation") is True

    state = update_account_state(
        path,
        {"balance": 1000.0, "equity": 920.0},
        config,
    )
    assert state["drawdown_from_initial"] < 0.1
    assert state.get("capital_preservation") is False
