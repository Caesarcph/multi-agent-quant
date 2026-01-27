"""Fusion engine with regime agreement and score thresholds."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable

from fusion.dynamic_weighting import load_dynamic_weights

ACTION_SCORE = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}


def _majority(items: list[str]) -> tuple[str | None, float]:
    if not items:
        return None, 0.0
    counts = Counter(items)
    winner, count = counts.most_common(1)[0]
    return winner, count / len(items)


def fuse_signals(signals: Iterable[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    signal_list = list(signals)
    if not signal_list:
        raise ValueError("No signals provided for fusion.")

    voting_cfg = config.get("voting", {})
    weights = voting_cfg.get("default_weights", {})
    dynamic_cfg = config.get("dynamic_weighting", {}) or {}
    if dynamic_cfg.get("enabled", False):
        weights_path = Path(dynamic_cfg.get("weights_path", "logs/dynamic_weights.json"))
        dynamic_weights = load_dynamic_weights(weights_path)
        if dynamic_weights:
            weights = dynamic_weights
    thresholds = voting_cfg.get("thresholds", {})

    buy_threshold = float(thresholds.get("buy", 0.35))
    sell_threshold = float(thresholds.get("sell", -0.35))

    scores = {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0}
    counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    total_weight = 0.0
    weighted_confidence = 0.0
    directional_score = 0.0

    regimes: list[str] = []

    for signal in signal_list:
        action = signal.get("action")
        if action not in ACTION_SCORE:
            raise ValueError(f"Unsupported action: {action}")
        model = signal.get("model")
        weight = float(weights.get(model, 1.0))

        scores[action] += weight
        counts[action] += 1
        total_weight += weight
        directional_score += ACTION_SCORE[action] * weight
        weighted_confidence += weight * float(signal.get("confidence", 0.0))

        regime = signal.get("context", {}).get("regime")
        if regime:
            regimes.append(regime)

    normalized_score = directional_score / total_weight if total_weight else 0.0
    avg_confidence = weighted_confidence / total_weight if total_weight else 0.0

    if normalized_score >= buy_threshold:
        action = "BUY"
    elif normalized_score <= sell_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    regime_cfg = config.get("regime_agreement", {})
    required = bool(regime_cfg.get("required", False))
    min_ratio = float(regime_cfg.get("min_agreement_ratio", 1.0))
    fallback_action = regime_cfg.get("fallback_action", "HOLD")

    regime_value, regime_ratio = _majority(regimes)
    regime_ok = (not required) or (regime_ratio >= min_ratio)
    if required and not regime_ok:
        action = fallback_action

    agreement_ratio = scores.get(action, 0.0) / total_weight if total_weight else 0.0

    def pick_representative(chosen_action: str) -> Dict[str, Any]:
        candidates = [s for s in signal_list if s.get("action") == chosen_action]
        if not candidates:
            candidates = signal_list
        return max(candidates, key=lambda item: float(item.get("confidence", 0.0)))

    representative = pick_representative(action)

    minority_cfg = config.get("minority_logging", {})
    minority_signals: list[Dict[str, Any]] = []
    if minority_cfg.get("enabled", False):
        minority_signals = [signal for signal in signal_list if signal.get("action") != action]

    return {
        "action": action,
        "agreement_ratio": agreement_ratio,
        "average_confidence": avg_confidence,
        "scores": scores,
        "counts": counts,
        "directional_score": normalized_score,
        "regime": regime_value,
        "regime_agreement_ratio": regime_ratio,
        "regime_ok": regime_ok,
        "representative_signal": representative,
        "minority_signals": minority_signals,
    }
