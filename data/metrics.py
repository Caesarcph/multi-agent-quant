"""Summarize cycle metrics."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict


def summarize_cycle(result: Dict[str, Any]) -> Dict[str, Any]:
    signals = result.get("signals", [])
    errors = result.get("errors", [])
    risk_notes = result.get("risk_notes", [])
    execution = result.get("execution")

    actions = [signal.get("action") for signal in signals if signal.get("action")]
    action_counts = Counter(actions)
    risk_counts = Counter(risk_notes)

    summary = {
        "signal_count": len(signals),
        "error_count": len(errors),
        "actions": dict(action_counts),
        "risk_notes": dict(risk_counts),
        "final_action": result.get("final_signal", {}).get("action"),
        "execution_status": execution.get("status") if execution else None,
    }
    return summary
