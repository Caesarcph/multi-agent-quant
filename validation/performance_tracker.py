"""Performance metric calculations."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def compute_metrics(
    trades: List[Dict[str, Any]],
    initial_balance: Optional[float] = None,
) -> Dict[str, Any]:
    profits = [float(trade.get("profit", 0.0)) for trade in trades]
    total_trades = len(profits)
    win_trades = [p for p in profits if p > 0]
    loss_trades = [p for p in profits if p < 0]

    win_rate = (len(win_trades) / total_trades) if total_trades else 0.0
    gross_profit = sum(win_trades)
    gross_loss = abs(sum(loss_trades))
    profit_factor = None if gross_loss == 0 else (gross_profit / gross_loss)

    avg_profit = sum(profits) / total_trades if total_trades else 0.0
    std_profit = _stddev(profits)
    sharpe_ratio = None
    if std_profit > 0:
        sharpe_ratio = (avg_profit / std_profit) * math.sqrt(total_trades)

    equity_curve = _build_equity_curve(profits, initial_balance)
    max_drawdown = _max_drawdown(equity_curve)

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": sum(profits),
    }


def _build_equity_curve(profits: List[float], initial_balance: Optional[float]) -> List[float]:
    equity = float(initial_balance) if initial_balance is not None else 0.0
    curve = []
    for profit in profits:
        equity += float(profit)
        curve.append(equity)
    return curve


def _max_drawdown(curve: List[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        if value > peak:
            peak = value
        if peak != 0:
            drawdown = (peak - value) / abs(peak)
            max_dd = max(max_dd, drawdown)
    return max_dd


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def save_metrics(path: Path, metrics: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_metrics(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
