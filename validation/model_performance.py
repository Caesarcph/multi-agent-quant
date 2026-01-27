"""Model performance evaluation from cycle signals."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import mt5_connection, mt5_tools


def evaluate_model_performance(
    cycles: List[Dict[str, Any]],
    mt5_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    timeframe = config.get("timeframe", "M1")
    default_ttl = int(config.get("default_ttl_minutes", 15))
    window_signals = int(config.get("window_signals", 500))
    min_samples = int(config.get("min_samples", 5))

    signals = _collect_signals(cycles, window_signals)
    stats_by_model: Dict[str, Dict[str, Any]] = {}
    stats_by_regime: Dict[str, Dict[str, Any]] = {}
    stats_by_model_regime: Dict[str, Dict[str, Any]] = {}

    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required for model performance")

    symbol_cache: Dict[str, Dict[str, Any]] = {}

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        for signal in signals:
            action = signal.get("action")
            if action not in {"BUY", "SELL"}:
                continue
            symbol = signal.get("symbol")
            if not symbol:
                continue
            timestamp = _parse_time(signal.get("timestamp"))
            if timestamp is None:
                continue
            ttl_minutes = int(signal.get("ttl_minutes", default_ttl))
            exit_time = timestamp + timedelta(minutes=ttl_minutes)

            entry_rate = mt5_tools.get_rate_at(symbol, timeframe, timestamp)
            exit_rate = mt5_tools.get_rate_at(symbol, timeframe, exit_time)
            if entry_rate is None or exit_rate is None:
                continue

            entry_price = float(entry_rate.get("close", 0.0))
            exit_price = float(exit_rate.get("close", 0.0))
            if entry_price <= 0 or exit_price <= 0:
                continue

            symbol_info = symbol_cache.get(symbol)
            if symbol_info is None:
                symbol_info = mt5_tools.get_symbol_info(symbol)
                symbol_cache[symbol] = symbol_info

            pip_size = _pip_size(symbol_info)
            pip_value = _pip_value(symbol_info)
            volume = float(signal.get("volume", 1.0))
            pips_move = (exit_price - entry_price) / pip_size
            if action == "SELL":
                pips_move = -pips_move
            profit = pips_move * pip_value * volume

            model = str(signal.get("model", "unknown"))
            regime = str(signal.get("context", {}).get("regime", "unknown"))
            confidence = float(signal.get("confidence", 0.0))

            _update_stats(stats_by_model, model, pips_move, profit, confidence)
            _update_stats(stats_by_regime, regime, pips_move, profit, confidence)
            key = f"{model}:{regime}"
            _update_stats(stats_by_model_regime, key, pips_move, profit, confidence)
    finally:
        mt5_connection.shutdown()

    def finalize(stats: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        output: Dict[str, Dict[str, Any]] = {}
        for key, values in stats.items():
            wins = values.get("wins", 0)
            losses = values.get("losses", 0)
            ties = values.get("ties", 0)
            total = wins + losses + ties
            if wins + losses >= min_samples:
                accuracy = wins / (wins + losses) if (wins + losses) else 0.0
            else:
                accuracy = None
            avg_conf = values.get("confidence_sum", 0.0) / total if total else 0.0
            output[key] = {
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "total": total,
                "accuracy": accuracy,
                "profit_sum": values.get("profit_sum", 0.0),
                "avg_confidence": avg_conf,
            }
        return output

    return {
        "by_model": finalize(stats_by_model),
        "by_regime": finalize(stats_by_regime),
        "by_model_regime": finalize(stats_by_model_regime),
        "window_signals": len(signals),
    }


def _update_stats(
    stats: Dict[str, Dict[str, Any]],
    key: str,
    pips_move: float,
    profit: float,
    confidence: float,
) -> None:
    entry = stats.setdefault(
        key, {"wins": 0, "losses": 0, "ties": 0, "profit_sum": 0.0, "confidence_sum": 0.0}
    )
    if pips_move > 0:
        entry["wins"] += 1
    elif pips_move < 0:
        entry["losses"] += 1
    else:
        entry["ties"] += 1
    entry["profit_sum"] += float(profit)
    entry["confidence_sum"] += float(confidence)


def _collect_signals(cycles: List[Dict[str, Any]], window_signals: int) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    for cycle in cycles:
        collected.extend(cycle.get("signals", []) or [])
    if window_signals <= 0:
        return collected
    return collected[-window_signals:]


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _pip_size(symbol_info: Dict[str, Any]) -> float:
    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    return point * 10 if digits in (3, 5) else point


def _pip_value(symbol_info: Dict[str, Any]) -> float:
    tick_size = float(symbol_info.get("trade_tick_size", 0.0))
    tick_value = float(symbol_info.get("trade_tick_value", 0.0))
    pip_size = _pip_size(symbol_info)
    if tick_size <= 0.0 or tick_value <= 0.0:
        return 1.0
    return tick_value * (pip_size / tick_size)
