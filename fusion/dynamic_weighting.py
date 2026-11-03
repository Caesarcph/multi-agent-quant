"""Dynamic weighting utilities based on signal outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validation.model_performance import evaluate_model_performance


@dataclass
class WeightUpdateResult:
    weights: dict[str, float]
    stats: dict[str, Any]


def load_dynamic_weights(path: Path) -> dict[str, float] | None:
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
    accuracy: dict[str, float],
    min_weight: float,
    max_weight: float,
    default_weights: dict[str, float],
) -> dict[str, float]:
    weights: dict[str, float] = {}
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
    cycles: list[dict[str, Any]],
    output_path: Path,
    mt5_cfg: dict[str, Any],
    config: dict[str, Any],
    default_weights: dict[str, float],
) -> WeightUpdateResult:
    stats = evaluate_model_performance(cycles, mt5_cfg, config)
    accuracy = {model: data.get("accuracy") for model, data in stats.get("by_model", {}).items()}
    min_weight = float(config.get("min_weight", 0.1))
    max_weight = float(config.get("max_weight", 0.4))

    weights = compute_weights(accuracy, min_weight, max_weight, default_weights)
    payload = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "weights": weights,
        "stats": stats,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return WeightUpdateResult(weights=weights, stats=stats)


def evaluate_signal_outcomes(
    cycles: list[dict[str, Any]],
    mt5_cfg: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    return evaluate_model_performance(cycles, mt5_cfg, config)
