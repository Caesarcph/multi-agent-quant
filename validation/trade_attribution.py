"""Attribute MT5 deals to prior signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AttributionResult:
    attributed: List[Dict[str, Any]]
    unmatched: List[Dict[str, Any]]
    summary: Dict[str, Any]


def attribute_deals(
    cycles: List[Dict[str, Any]],
    deals: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> AttributionResult:
    use_final = bool(config.get("use_final_signal", True))
    match_window = int(config.get("match_window_minutes", 60))
    comment_contains = config.get("comment_contains")
    magic = config.get("magic")

    signals = _collect_signals(cycles, use_final)
    signals_by_symbol = _group_signals(signals)

    attributed: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []

    for deal in deals:
        if magic is not None and deal.get("magic") != magic:
            continue
        if comment_contains and comment_contains not in str(deal.get("comment", "")):
            continue

        symbol = deal.get("symbol")
        if not symbol:
            unmatched.append(deal)
            continue
        action = _deal_action(deal)
        if action is None:
            unmatched.append(deal)
            continue
        deal_time = _deal_time(deal.get("time"))
        if deal_time is None:
            unmatched.append(deal)
            continue

        candidate = _find_candidate(
            signals_by_symbol.get(symbol, []),
            action,
            deal_time,
            match_window,
        )
        if candidate is None:
            unmatched.append(deal)
            continue

        attributed.append(
            {
                "deal": deal,
                "signal": candidate,
            }
        )

    summary = _summarize(attributed)
    return AttributionResult(attributed=attributed, unmatched=unmatched, summary=summary)


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


def _group_signals(signals: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for signal in signals:
        action = signal.get("action")
        symbol = signal.get("symbol")
        timestamp = _parse_time(signal.get("timestamp"))
        if action not in {"BUY", "SELL"} or not symbol or timestamp is None:
            continue
        signal["__timestamp"] = timestamp
        grouped.setdefault(symbol, []).append(signal)
    for sym_signals in grouped.values():
        sym_signals.sort(key=lambda item: item["__timestamp"])
    return grouped


def _find_candidate(
    signals: List[Dict[str, Any]],
    action: str,
    deal_time: datetime,
    match_window: int,
) -> Optional[Dict[str, Any]]:
    window_start = deal_time - timedelta(minutes=match_window)
    candidates = [
        signal
        for signal in signals
        if signal.get("action") == action
        and window_start <= signal["__timestamp"] <= deal_time
    ]
    if not candidates:
        return None
    return candidates[-1]


def _deal_action(deal: Dict[str, Any]) -> Optional[str]:
    deal_type = deal.get("type")
    if deal_type is None:
        return None
    # MT5: 0=BUY, 1=SELL
    if int(deal_type) == 0:
        return "BUY"
    if int(deal_type) == 1:
        return "SELL"
    return None


def _deal_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _summarize(attributed: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_model: Dict[str, Dict[str, float]] = {}
    for item in attributed:
        signal = item.get("signal", {})
        deal = item.get("deal", {})
        model = str(signal.get("model", "unknown"))
        stats = by_model.setdefault(model, {"count": 0, "profit_sum": 0.0})
        stats["count"] += 1
        stats["profit_sum"] += float(deal.get("profit", 0.0))
    return {"by_model": by_model, "attributed_count": len(attributed)}
