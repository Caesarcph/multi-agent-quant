"""Audit log helpers for agent outputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...<truncated>"


def log_agent_output(
    output_dir: Path,
    agent_name: str,
    model: str,
    prompt: str,
    raw_output: str,
    reasoning: str | None = None,
    max_chars: int = 4000,
    error: str | None = None,
    repair_output: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{stamp}_{agent_name}.json"

    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "agent": agent_name,
        "model": model,
        "prompt": _truncate(prompt, max_chars),
        "raw_output": _truncate(raw_output, max_chars),
        "reasoning": _truncate(reasoning, max_chars),
        "error": _truncate(error, max_chars),
        "repair_output": _truncate(repair_output, max_chars),
    }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
