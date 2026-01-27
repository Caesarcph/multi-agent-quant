"""MT5-backed signal replay backtesting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tools import mt5_connection, mt5_tools
from validation.backtester import run_backtest

TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}


def run_mt5_backtest(
    cycles: List[Dict[str, Any]],
    mt5_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    use_final = bool(config.get("use_final_signal", True))
    models = set(config.get("models") or [])
    symbols_filter = set(config.get("symbols") or [])
    default_ttl = int(config.get("default_ttl_minutes", 15))

    signals = _collect_signals(cycles, use_final)
    grouped = _group_signals(signals, models, symbols_filter)

    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required for backtest")

    results: Dict[str, Any] = {"symbols": {}, "summary": {}}

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        for (symbol, timeframe), signal_list in grouped.items():
            if not signal_list:
                continue
            earliest, latest = _signal_time_bounds(signal_list, default_ttl)
            if earliest is None or latest is None:
                continue
            price_series = _load_price_series(symbol, timeframe, earliest, latest)
            if not price_series:
                continue

            signal_list = sorted(
                signal_list, key=lambda item: item.get("timestamp") or ""
            )
            result = run_backtest(
                signals=signal_list,
                price_series=price_series,
                initial_balance=float(config.get("initial_balance", 0.0) or 0.0),
                price_multiplier=float(config.get("price_multiplier", 1.0)),
            )
            key = f"{symbol}:{timeframe}"
            results["symbols"][key] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "trade_count": len(result.trades),
                "metrics": result.metrics,
            }
    finally:
        mt5_connection.shutdown()

    results["summary"] = _aggregate_metrics(results["symbols"])
    return results


def _collect_signals(cycles: List[Dict[str, Any]], use_final: bool) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    for cycle in cycles:
        if use_final:
            signal = cycle.get("final_signal")
            if isinstance(signal, dict):
                collected.append(signal)
        else:
            collected.extend(cycle.get("signals", []) or [])
    return collected


def _group_signals(
    signals: List[Dict[str, Any]],
    models: set[str],
    symbols_filter: set[str],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for signal in signals:
        action = signal.get("action")
        if action not in {"BUY", "SELL"}:
            continue
        model = signal.get("model")
        if models and model not in models:
            continue
        symbol = signal.get("symbol")
        timeframe = signal.get("timeframe", "M1")
        if not symbol:
            continue
        if symbols_filter and symbol not in symbols_filter:
            continue
        grouped.setdefault((symbol, timeframe), []).append(signal)
    return grouped


def _signal_time_bounds(
    signals: List[Dict[str, Any]],
    default_ttl: int,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    earliest = None
    latest = None
    for signal in signals:
        timestamp = _parse_time(signal.get("timestamp"))
        if timestamp is None:
            continue
        ttl_minutes = int(signal.get("ttl_minutes", default_ttl))
        exit_time = timestamp + timedelta(minutes=ttl_minutes)
        if earliest is None or timestamp < earliest:
            earliest = timestamp
        if latest is None or exit_time > latest:
            latest = exit_time
    return earliest, latest


def _load_price_series(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    tf_minutes = TIMEFRAME_MINUTES.get(str(timeframe).upper(), 1)
    total_minutes = max(int((end - start).total_seconds() // 60), 1)
    count = int(total_minutes / tf_minutes) + 5
    return mt5_tools.get_rates_from(symbol, timeframe, start, count=count)


def _aggregate_metrics(symbol_metrics: Dict[str, Any]) -> Dict[str, Any]:
    totals = {
        "total_trades": 0,
        "net_profit": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
    }
    for item in symbol_metrics.values():
        metrics = item.get("metrics", {})
        totals["total_trades"] += int(metrics.get("total_trades", 0))
        totals["net_profit"] += float(metrics.get("net_profit", 0.0))
        totals["gross_profit"] += float(metrics.get("gross_profit", 0.0))
        totals["gross_loss"] += float(metrics.get("gross_loss", 0.0))
    return totals


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
