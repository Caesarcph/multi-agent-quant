"""Simple majority fusion logic."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

VOTE_MAP = {"BUY": 1, "HOLD": 0, "SELL": -1}


def fuse_signals(
    signals: Iterable[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    signal_list = list(signals)
    if not signal_list:
        raise ValueError("No signals provided for fusion.")

    weights = weights or {}
    scores = {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0}
    counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    total_weight = 0.0
    weighted_confidence = 0.0

    for signal in signal_list:
        action = signal.get("action")
        if action not in scores:
            raise ValueError(f"Unsupported action: {action}")
        model = signal.get("model")
        weight = float(weights.get(model, 1.0))
        scores[action] += weight
        counts[action] += 1
        total_weight += weight
        weighted_confidence += weight * float(signal.get("confidence", 0.0))

    best_action = max(scores, key=lambda key: scores[key])
    ordered_scores = sorted(scores.values(), reverse=True)
    if len(ordered_scores) > 1 and ordered_scores[0] == ordered_scores[1]:
        best_action = "HOLD"

    agreement_ratio = scores[best_action] / total_weight if total_weight else 0.0
    avg_confidence = weighted_confidence / total_weight if total_weight else 0.0

    def pick_representative(action: str) -> Dict[str, Any]:
        candidates = [signal for signal in signal_list if signal.get("action") == action]
        if not candidates:
            candidates = signal_list
        return max(candidates, key=lambda item: float(item.get("confidence", 0.0)))

    representative = pick_representative(best_action)

    return {
        "action": best_action,
        "agreement_ratio": agreement_ratio,
        "average_confidence": avg_confidence,
        "scores": scores,
        "counts": counts,
        "representative_signal": representative,
    }
