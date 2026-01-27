"""Tests for daily guard."""
from __future__ import annotations

from datetime import datetime, timezone

from risk.daily_guard import check_daily_limits, compute_daily_stats


def test_daily_limits_blocks_trades() -> None:
    signal = {"action": "BUY"}
    stats = {"trades_today": 5, "pnl_today": -100.0, "volume_today": 3.0}
    config = {
        "max_daily_trades": 5,
        "daily_loss_limit": 10.0,
        "daily_profit_target": 2.0,
        "max_daily_volume": 2.5,
    }
    notes = check_daily_limits(signal, stats, config)
    assert "max_daily_trades_reached" in notes
    assert "daily_loss_limit_reached" in notes
    assert "max_daily_volume_reached" in notes


def test_compute_daily_stats() -> None:
    now = datetime.now(timezone.utc)
    trades = [
        {"time": now.timestamp(), "profit": 10.0, "volume": 0.5},
        {"time": now.timestamp(), "profit": -2.0, "volume": 0.25},
    ]
    stats = compute_daily_stats(trades)
    assert stats["trades_today"] == 2
    assert abs(stats["pnl_today"] - 8.0) < 1e-6
    assert abs(stats["volume_today"] - 0.75) < 1e-6
