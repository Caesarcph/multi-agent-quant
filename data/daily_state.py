"""Daily stats persistence utilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def load_daily_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_daily_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def update_daily_state(
    path: Path,
    stats: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    today = now.date().isoformat()
    state = load_daily_state(path)
    if state.get("date") != today:
        state = {"date": today, "trades_today": 0, "pnl_today": 0.0}

    state["trades_today"] = int(stats.get("trades_today", 0))
    state["pnl_today"] = float(stats.get("pnl_today", 0.0))
    save_daily_state(path, state)
    return state
