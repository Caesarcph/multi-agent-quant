"""Tests for dynamic weighting utilities."""
from __future__ import annotations

from fusion.dynamic_weighting import compute_weights


def test_compute_weights_normalizes() -> None:
    accuracy = {"gpt": 0.6, "claude": 0.4}
    default = {"gpt": 0.25, "claude": 0.25, "gemini": 0.25, "deepseek": 0.25}
    weights = compute_weights(accuracy, min_weight=0.1, max_weight=0.4, default_weights=default)
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-6
    assert weights["gpt"] > weights["claude"]
