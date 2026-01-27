"""Tests for simple fusion logic."""
from __future__ import annotations

from fusion.simple_fusion import fuse_signals


def test_fuse_majority_buy() -> None:
    signals = [
        {"model": "gpt", "action": "BUY", "confidence": 0.6},
        {"model": "claude", "action": "BUY", "confidence": 0.7},
        {"model": "gemini", "action": "SELL", "confidence": 0.4},
    ]
    result = fuse_signals(signals)
    assert result["action"] == "BUY"
    assert result["counts"]["BUY"] == 2


def test_fuse_tie_defaults_to_hold() -> None:
    signals = [
        {"model": "gpt", "action": "BUY", "confidence": 0.6},
        {"model": "claude", "action": "SELL", "confidence": 0.6},
    ]
    result = fuse_signals(signals)
    assert result["action"] == "HOLD"
