"""Daily risk guards.

``daily_loss_limit`` and ``daily_profit_target`` in ``risk_config.yaml`` are
fractions of the balance at the start of the day (0.03 = 3%), matching the
hard limit in ``maq_core.config.limits``. ``max_daily_volume`` is in lots.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# MT5 deal types that are trades; balance, credit, bonus, ... operations are not.
_TRADE_DEAL_TYPES = {0, 1}  # DEAL_TYPE_BUY, DEAL_TYPE_SELL
_ENTRY_IN = 0  # DEAL_ENTRY_IN: the deal opened (or added to) a position


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

    pnl_fraction = stats.get("pnl_today_fraction")
    daily_loss_limit = config.get("daily_loss_limit")
    if daily_loss_limit is not None:
        if pnl_fraction is None:
            # Without the balance the limit cannot be checked: do not open new risk.
            notes.append("daily_loss_limit_unchecked:no_balance")
        elif float(pnl_fraction) <= -abs(float(daily_loss_limit)):
            notes.append("daily_loss_limit_reached")

    daily_profit_target = config.get("daily_profit_target")
    if daily_profit_target is not None and pnl_fraction is not None:
        if float(pnl_fraction) >= abs(float(daily_profit_target)):
            notes.append("daily_profit_target_reached")

    max_daily_volume = config.get("max_daily_volume")
    if max_daily_volume is not None:
        volume_today = float(stats.get("volume_today", 0.0))
        if volume_today >= float(max_daily_volume):
            notes.append("max_daily_volume_reached")

    return notes


def compute_daily_stats(
    trades: list[dict[str, Any]],
    balance: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarise today's MT5 deals.

    ``pnl_today`` is net of commission, swap and fees; deposits and other
    balance operations are ignored. Only opening deals count as trades and
    towards ``volume_today``. With the current ``balance``, the result also has
    ``start_balance`` and ``pnl_today_fraction`` (P&L relative to the balance
    at the start of the day), which ``check_daily_limits`` needs.
    """
    today = (now or datetime.now(UTC)).date()
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
        deal_type = trade.get("type")
        if deal_type is not None and int(deal_type) not in _TRADE_DEAL_TYPES:
            continue
        pnl_today += sum(
            float(trade.get(key) or 0.0) for key in ("profit", "commission", "swap", "fee")
        )
        entry = trade.get("entry")
        if entry is None or int(entry) == _ENTRY_IN:
            trades_today += 1
            volume_today += float(trade.get("volume", 0.0))

    stats: dict[str, Any] = {
        "trades_today": trades_today,
        "pnl_today": pnl_today,
        "volume_today": volume_today,
        "pnl_today_fraction": None,
    }
    if balance is not None:
        # The balance already contains today's realised P&L.
        start_balance = float(balance) - pnl_today
        stats["start_balance"] = start_balance
        if start_balance > 0:
            stats["pnl_today_fraction"] = pnl_today / start_balance
    return stats
