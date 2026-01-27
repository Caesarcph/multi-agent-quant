"""Cycle report generation utilities."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def load_cycles(cycle_dir: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not cycle_dir.exists():
        return []
    files = sorted(cycle_dir.glob("*.json"))
    if limit is not None:
        files = files[-limit:]
    cycles: List[Dict[str, Any]] = []
    for path in files:
        try:
            cycles.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return cycles


def aggregate_cycles(cycles: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    cycles_list = list(cycles)
    action_counts: Counter[str] = Counter()
    execution_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    total_signals = 0
    total_errors = 0
    agreement_sum = 0.0
    agreement_count = 0

    timestamps: List[str] = []

    for cycle in cycles_list:
        final_action = cycle.get("final_signal", {}).get("action")
        if final_action:
            action_counts[str(final_action)] += 1

        execution = cycle.get("execution") or {}
        status = execution.get("status")
        if status:
            execution_counts[str(status)] += 1

        risk_notes = cycle.get("risk_notes", []) or []
        risk_counts.update([str(note) for note in risk_notes])

        signals = cycle.get("signals", []) or []
        total_signals += len(signals)
        for signal in signals:
            model = signal.get("model")
            if model:
                agent_counts[str(model)] += 1

        errors = cycle.get("errors", []) or []
        total_errors += len(errors)

        fusion = cycle.get("fusion", {}) or {}
        agreement = fusion.get("agreement_ratio")
        if agreement is not None:
            agreement_sum += float(agreement)
            agreement_count += 1

        stamp = cycle.get("snapshot_meta", {}).get("timestamp")
        if stamp:
            timestamps.append(str(stamp))

    avg_agreement = agreement_sum / agreement_count if agreement_count else None
    timestamps_sorted = sorted(timestamps)

    return {
        "cycle_count": len(cycles_list),
        "total_signals": total_signals,
        "total_errors": total_errors,
        "final_action_counts": dict(action_counts),
        "execution_status_counts": dict(execution_counts),
        "risk_note_counts": dict(risk_counts),
        "agent_counts": dict(agent_counts),
        "average_agreement_ratio": avg_agreement,
        "first_timestamp": timestamps_sorted[0] if timestamps_sorted else None,
        "last_timestamp": timestamps_sorted[-1] if timestamps_sorted else None,
    }


def render_markdown_report(
    report_date: str,
    summary: Dict[str, Any],
    daily_state: Optional[Dict[str, Any]] = None,
) -> str:
    lines: List[str] = []
    lines.append(f"# Daily Report ({report_date})")
    lines.append("")

    lines.append("## Summary")
    lines.append(f"- Cycles: {summary.get('cycle_count', 0)}")
    lines.append(f"- Signals: {summary.get('total_signals', 0)}")
    lines.append(f"- Errors: {summary.get('total_errors', 0)}")

    avg_agreement = summary.get("average_agreement_ratio")
    if avg_agreement is not None:
        lines.append(f"- Average agreement: {avg_agreement:.2f}")

    lines.append("")

    if daily_state:
        lines.append("## Daily State")
        lines.append(f"- Trades today: {daily_state.get('trades_today', 0)}")
        lines.append(f"- PnL today: {daily_state.get('pnl_today', 0.0)}")
        lines.append("")

    def render_counter(title: str, data: Dict[str, Any]) -> None:
        lines.append(f"## {title}")
        if not data:
            lines.append("- (none)")
            lines.append("")
            return
        for key, value in data.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    render_counter("Final Actions", summary.get("final_action_counts", {}))
    render_counter("Execution Status", summary.get("execution_status_counts", {}))
    render_counter("Risk Notes", summary.get("risk_note_counts", {}))
    render_counter("Agent Counts", summary.get("agent_counts", {}))

    return "\n".join(lines).strip() + "\n"


def build_report_payload(
    report_date: str,
    summary: Dict[str, Any],
    daily_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "report_date": report_date,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": summary,
        "daily_state": daily_state,
    }


def load_daily_state(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_json_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")
