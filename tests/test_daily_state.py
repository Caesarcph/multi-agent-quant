"""Tests for daily state persistence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from data.daily_state import update_daily_state


def test_update_daily_state_resets(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    old_state = {"date": "2000-01-01", "trades_today": 10, "pnl_today": -5.0}
    state_path.write_text(__import__("json").dumps(old_state), encoding="utf-8")

    stats = {"trades_today": 2, "pnl_today": 3.0}
    state = update_daily_state(state_path, stats, now=datetime.now(timezone.utc))
    assert state["trades_today"] == 2
    assert state["pnl_today"] == 3.0


def test_update_daily_state_same_day(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    state_path = tmp_path / "state.json"
    state_path.write_text(
        __import__("json").dumps({"date": today, "trades_today": 1, "pnl_today": 0.5}),
        encoding="utf-8",
    )
    stats = {"trades_today": 3, "pnl_today": -1.0}
    state = update_daily_state(state_path, stats, now=now)
    assert state["date"] == today
    assert state["trades_today"] == 3
    assert state["pnl_today"] == -1.0
