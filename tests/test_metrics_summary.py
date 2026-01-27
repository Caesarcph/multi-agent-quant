"""Tests for cycle metrics summary."""
from __future__ import annotations

from data.metrics import summarize_cycle


def test_summarize_cycle_counts() -> None:
    result = {
        "signals": [
            {"action": "BUY"},
            {"action": "SELL"},
            {"action": "BUY"},
        ],
        "errors": [{"agent": "x"}],
        "risk_notes": ["a", "a", "b"],
        "final_signal": {"action": "HOLD"},
        "execution": {"status": "dry_run"},
    }
    summary = summarize_cycle(result)
    assert summary["signal_count"] == 3
    assert summary["error_count"] == 1
    assert summary["actions"]["BUY"] == 2
    assert summary["risk_notes"]["a"] == 2
    assert summary["final_action"] == "HOLD"
    assert summary["execution_status"] == "dry_run"
