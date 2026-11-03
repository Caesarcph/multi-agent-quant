"""Lightweight backtester for signal replay."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from validation.performance_tracker import compute_metrics


@dataclass
class BacktestResult:
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]


def run_backtest(
    signals: list[dict[str, Any]],
    price_series: list[dict[str, Any]],
    initial_balance: float = 0.0,
    price_multiplier: float = 1.0,
) -> BacktestResult:
    prices, timestamps = _prepare_prices(price_series)
    open_position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    for signal in signals:
        action = signal.get("action")
        if action not in {"BUY", "SELL"}:
            continue
        ts = _parse_time(signal.get("timestamp"))
        if ts is None:
            continue
        price = _lookup_price(prices, timestamps, ts)
        if price is None:
            continue

        if open_position and open_position["direction"] != action:
            trades.append(_close_position(open_position, price, price_multiplier))
            open_position = None

        if open_position is None:
            open_position = {
                "direction": action,
                "entry_price": price,
                "volume": float(signal.get("volume", 1.0)),
                "entry_time": ts.isoformat(),
            }

    if open_position and prices:
        trades.append(_close_position(open_position, prices[-1], price_multiplier))

    metrics = compute_metrics(trades, initial_balance=initial_balance)
    return BacktestResult(trades=trades, metrics=metrics)


def _prepare_prices(price_series: list[dict[str, Any]]) -> tuple[list[float], list[datetime]]:
    prices: list[float] = []
    timestamps: list[datetime] = []
    for row in price_series:
        ts = _parse_time(row.get("time"))
        if ts is None:
            continue
        price = row.get("close")
        if price is None:
            continue
        timestamps.append(ts)
        prices.append(float(price))
    return prices, timestamps


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _lookup_price(prices: list[float], timestamps: list[datetime], ts: datetime) -> float | None:
    if not timestamps:
        return None
    idx = bisect.bisect_right(timestamps, ts) - 1
    if idx < 0:
        return None
    return prices[idx]


def _close_position(
    position: dict[str, Any], exit_price: float, multiplier: float
) -> dict[str, Any]:
    direction = position["direction"]
    entry_price = float(position["entry_price"])
    volume = float(position.get("volume", 1.0))
    if direction == "BUY":
        profit = (exit_price - entry_price) * volume * multiplier
    else:
        profit = (entry_price - exit_price) * volume * multiplier
    return {
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "volume": volume,
        "profit": profit,
        "entry_time": position.get("entry_time"),
        "exit_time": datetime.utcnow().isoformat() + "Z",
    }
