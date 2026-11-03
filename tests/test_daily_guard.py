"""Tests for daily guard."""

from __future__ import annotations

from datetime import UTC, datetime

from risk.daily_guard import check_daily_limits, compute_daily_stats

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
TODAY = NOW.timestamp()


def deal(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {"time": TODAY, "type": 0, "entry": 0, "volume": 0.1, "profit": 0.0}
    base.update(fields)
    return base


def test_daily_limits_blocks_trades() -> None:
    signal = {"action": "BUY"}
    stats = {
        "trades_today": 5,
        "pnl_today": -400.0,
        "pnl_today_fraction": -0.04,
        "volume_today": 3.0,
    }
    config = {
        "max_daily_trades": 5,
        "daily_loss_limit": 0.03,
        "daily_profit_target": 0.02,
        "max_daily_volume": 2.5,
    }
    notes = check_daily_limits(signal, stats, config)
    assert "max_daily_trades_reached" in notes
    assert "daily_loss_limit_reached" in notes
    assert "max_daily_volume_reached" in notes
    assert "daily_profit_target_reached" not in notes


def test_daily_loss_limit_is_a_fraction_of_the_starting_balance() -> None:
    """Regression: 0.03 used to be compared with money, so losing 4 cents stopped the day."""
    config = {"daily_loss_limit": 0.03}
    small_loss = compute_daily_stats([deal(entry=1, profit=-5.0)], balance=9_995.0, now=NOW)
    assert check_daily_limits({"action": "BUY"}, small_loss, config) == []

    # 10,000 at the start of the day, 310 lost: 3.1% > 3%.
    big_loss = compute_daily_stats([deal(entry=1, profit=-310.0)], balance=9_690.0, now=NOW)
    assert big_loss["start_balance"] == 10_000.0
    assert abs(big_loss["pnl_today_fraction"] + 0.031) < 1e-12
    assert check_daily_limits({"action": "SELL"}, big_loss, config) == ["daily_loss_limit_reached"]


def test_daily_loss_limit_fails_closed_without_a_balance() -> None:
    stats = compute_daily_stats([], now=NOW)
    notes = check_daily_limits({"action": "BUY"}, stats, {"daily_loss_limit": 0.03})
    assert notes == ["daily_loss_limit_unchecked:no_balance"]
    # HOLD never needs a check.
    assert check_daily_limits({"action": "HOLD"}, stats, {"daily_loss_limit": 0.03}) == []


def test_daily_profit_target_is_a_fraction_too() -> None:
    stats = compute_daily_stats([deal(entry=1, profit=250.0)], balance=10_250.0, now=NOW)
    assert check_daily_limits({"action": "BUY"}, stats, {"daily_profit_target": 0.02}) == [
        "daily_profit_target_reached"
    ]
    assert check_daily_limits({"action": "BUY"}, stats, {"daily_profit_target": 0.03}) == []


def test_compute_daily_stats() -> None:
    trades = [
        {"time": TODAY, "profit": 10.0, "volume": 0.5},
        {"time": TODAY, "profit": -2.0, "volume": 0.25},
    ]
    stats = compute_daily_stats(trades, now=NOW)
    assert stats["trades_today"] == 2
    assert abs(stats["pnl_today"] - 8.0) < 1e-6
    assert abs(stats["volume_today"] - 0.75) < 1e-6
    assert stats["pnl_today_fraction"] is None


def test_daily_stats_count_net_trading_results_only() -> None:
    trades = [
        deal(type=2, entry=0, profit=1_000.0),  # deposit: not a trade, not profit
        deal(entry=0, volume=0.3, commission=-2.1),  # open
        deal(entry=1, volume=0.3, profit=45.0, commission=-2.1, swap=-0.8),  # close
        deal(entry=0, volume=0.2, commission=-1.4, time=TODAY - 86_400),  # yesterday
    ]
    stats = compute_daily_stats(trades, balance=10_040.0, now=NOW)
    assert stats["trades_today"] == 1  # one position opened today
    assert abs(stats["volume_today"] - 0.3) < 1e-9
    assert abs(stats["pnl_today"] - (45.0 - 2.1 - 2.1 - 0.8)) < 1e-9
