"""Lightweight backtester for signal replay."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from validation.performance_tracker import compute_metrics


@dataclass
class BacktestResult:
    trades: List[Dict[str, Any]]
    metrics: Dict[str, Any]


def run_backtest(
    signals: List[Dict[str, Any]],
    price_series: List[Dict[str, Any]],
    initial_balance: float = 0.0,
    price_multiplier: float = 1.0,
) -> BacktestResult:
    prices, timestamps = _prepare_prices(price_series)
    open_position: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []

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


def _prepare_prices(price_series: List[Dict[str, Any]]) -> tuple[List[float], List[datetime]]:
    prices: List[float] = []
    timestamps: List[datetime] = []
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


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _lookup_price(prices: List[float], timestamps: List[datetime], ts: datetime) -> Optional[float]:
    if not timestamps:
        return None
    idx = bisect.bisect_right(timestamps, ts) - 1
    if idx < 0:
        return None
    return prices[idx]


def _close_position(position: Dict[str, Any], exit_price: float, multiplier: float) -> Dict[str, Any]:
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
