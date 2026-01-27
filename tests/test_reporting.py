"""Tests for reporting summary."""
from __future__ import annotations

from data.reporting import aggregate_cycles, build_report_payload, render_markdown_report


def test_aggregate_cycles() -> None:
    cycles = [
        {
            "final_signal": {"action": "BUY"},
            "signals": [{"model": "gpt"}, {"model": "gpt"}],
            "errors": [],
            "risk_notes": ["a"],
            "fusion": {"agreement_ratio": 0.5},
            "snapshot_meta": {"timestamp": "2026-01-21T00:00:00Z"},
            "execution": {"status": "dry_run"},
        },
        {
            "final_signal": {"action": "HOLD"},
            "signals": [{"model": "claude"}],
            "errors": [{"agent": "x"}],
            "risk_notes": ["a", "b"],
            "fusion": {"agreement_ratio": 0.75},
            "snapshot_meta": {"timestamp": "2026-01-21T01:00:00Z"},
        },
    ]
    summary = aggregate_cycles(cycles)
    assert summary["cycle_count"] == 2
    assert summary["total_signals"] == 3
    assert summary["total_errors"] == 1
    assert summary["final_action_counts"]["BUY"] == 1
    assert summary["execution_status_counts"]["dry_run"] == 1


def test_render_markdown_report() -> None:
    summary = {
        "cycle_count": 1,
        "total_signals": 2,
        "total_errors": 0,
        "final_action_counts": {"BUY": 1},
        "execution_status_counts": {},
        "risk_note_counts": {},
        "agent_counts": {"gpt": 2},
        "average_agreement_ratio": 0.5,
    }
    output = render_markdown_report("20260121", summary, {"trades_today": 1, "pnl_today": 2.0})
    assert "Daily Report" in output
    assert "Trades today" in output


def test_build_report_payload() -> None:
    summary = {"cycle_count": 1}
    payload = build_report_payload("20260121", summary, {"trades_today": 1})
    assert payload["report_date"] == "20260121"
    assert payload["summary"]["cycle_count"] == 1
