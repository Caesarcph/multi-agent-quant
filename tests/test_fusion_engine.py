"""Tests for fusion engine."""
from __future__ import annotations

from fusion.fusion_engine import fuse_signals


def test_fusion_thresholds_buy() -> None:
    cfg = {
        "voting": {"thresholds": {"buy": 0.2, "sell": -0.2}, "default_weights": {}}
    }
    signals = [
        {"model": "gpt", "action": "BUY", "confidence": 0.6, "context": {"regime": "ranging"}},
        {"model": "claude", "action": "BUY", "confidence": 0.7, "context": {"regime": "ranging"}},
        {"model": "gemini", "action": "SELL", "confidence": 0.4, "context": {"regime": "ranging"}},
    ]
    result = fuse_signals(signals, cfg)
    assert result["action"] == "BUY"


def test_fusion_regime_required_fallback() -> None:
    cfg = {
        "regime_agreement": {"required": True, "min_agreement_ratio": 0.75, "fallback_action": "HOLD"},
        "voting": {"thresholds": {"buy": 0.2, "sell": -0.2}, "default_weights": {}},
    }
    signals = [
        {"model": "gpt", "action": "BUY", "confidence": 0.6, "context": {"regime": "ranging"}},
        {"model": "claude", "action": "BUY", "confidence": 0.7, "context": {"regime": "trending_up"}},
        {"model": "gemini", "action": "BUY", "confidence": 0.6, "context": {"regime": "volatile"}},
        {"model": "deepseek", "action": "BUY", "confidence": 0.6, "context": {"regime": "ranging"}},
    ]
    result = fuse_signals(signals, cfg)
    assert result["regime_ok"] is False
    assert result["action"] == "HOLD"
