"""Go-live criteria evaluation."""
from __future__ import annotations

from typing import Any, Dict, List


def check_go_live(metrics: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    failures: List[str] = []

    min_win_rate = config.get("min_win_rate")
    if min_win_rate is not None and float(metrics.get("win_rate", 0.0)) < float(min_win_rate):
        failures.append("min_win_rate")

    min_profit_factor = config.get("min_profit_factor")
    profit_factor = metrics.get("profit_factor")
    if min_profit_factor is not None:
        if profit_factor is None or float(profit_factor) < float(min_profit_factor):
            failures.append("min_profit_factor")

    max_drawdown = config.get("max_drawdown")
    if max_drawdown is not None and float(metrics.get("max_drawdown", 0.0)) > float(max_drawdown):
        failures.append("max_drawdown")

    min_sharpe = config.get("min_sharpe_ratio")
    sharpe_ratio = metrics.get("sharpe_ratio")
    if min_sharpe is not None:
        if sharpe_ratio is None or float(sharpe_ratio) < float(min_sharpe):
            failures.append("min_sharpe_ratio")

    return {"passed": not failures, "failures": failures}
