"""Daily risk guards."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def check_daily_limits(
    signal: dict[str, Any],
    stats: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    if signal.get("action") not in {"BUY", "SELL"}:
        return notes

    max_trades = config.get("max_daily_trades")
    if max_trades is not None:
        trades_today = int(stats.get("trades_today", 0))
        if trades_today >= int(max_trades):
            notes.append("max_daily_trades_reached")

    daily_loss_limit = config.get("daily_loss_limit")
    if daily_loss_limit is not None:
        pnl_today = float(stats.get("pnl_today", 0.0))
        if pnl_today <= -abs(float(daily_loss_limit)):
            notes.append("daily_loss_limit_reached")

    daily_profit_target = config.get("daily_profit_target")
    if daily_profit_target is not None:
        pnl_today = float(stats.get("pnl_today", 0.0))
        if pnl_today >= abs(float(daily_profit_target)):
            notes.append("daily_profit_target_reached")

    max_daily_volume = config.get("max_daily_volume")
    if max_daily_volume is not None:
        volume_today = float(stats.get("volume_today", 0.0))
        if volume_today >= float(max_daily_volume):
            notes.append("max_daily_volume_reached")

    return notes


def compute_daily_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    pnl_today = 0.0
    trades_today = 0
    volume_today = 0.0

    for trade in trades:
        trade_time = trade.get("time")
        if trade_time is None:
            continue
        try:
            trade_dt = datetime.fromtimestamp(float(trade_time), tz=UTC)
        except (TypeError, ValueError):
            continue
        if trade_dt.date() != today:
            continue
        trades_today += 1
        pnl_today += float(trade.get("profit", 0.0))
        volume_today += float(trade.get("volume", 0.0))

    return {"trades_today": trades_today, "pnl_today": pnl_today, "volume_today": volume_today}
