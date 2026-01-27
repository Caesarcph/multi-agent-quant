"""Dynamic weighting utilities based on signal outcomes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from validation.model_performance import evaluate_model_performance


@dataclass
class WeightUpdateResult:
    weights: Dict[str, float]
    stats: Dict[str, Any]


def load_dynamic_weights(path: Path) -> Optional[Dict[str, float]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    weights = payload.get("weights")
    if not isinstance(weights, dict):
        return None
    return {str(key): float(value) for key, value in weights.items()}


def compute_weights(
    accuracy: Dict[str, float],
    min_weight: float,
    max_weight: float,
    default_weights: Dict[str, float],
) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for model, base in default_weights.items():
        score = accuracy.get(model)
        if score is None:
            weights[model] = float(base)
        else:
            weights[model] = min_weight + (max_weight - min_weight) * float(score)

    total = sum(weights.values())
    if total <= 0:
        return default_weights
    return {model: value / total for model, value in weights.items()}


def update_dynamic_weights(
    cycles: List[Dict[str, Any]],
    output_path: Path,
    mt5_cfg: Dict[str, Any],
    config: Dict[str, Any],
    default_weights: Dict[str, float],
) -> WeightUpdateResult:
    stats = evaluate_model_performance(cycles, mt5_cfg, config)
    accuracy = {model: data.get("accuracy") for model, data in stats.get("by_model", {}).items()}
    min_weight = float(config.get("min_weight", 0.1))
    max_weight = float(config.get("max_weight", 0.4))

    weights = compute_weights(accuracy, min_weight, max_weight, default_weights)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "weights": weights,
        "stats": stats,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return WeightUpdateResult(weights=weights, stats=stats)


def evaluate_signal_outcomes(
    cycles: List[Dict[str, Any]],
    mt5_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    return evaluate_model_performance(cycles, mt5_cfg, config)
