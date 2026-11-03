"""Tests for fusion engine."""
from __future__ import annotations

from fusion.fusion_engine import decision_signal, fuse_signals


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


def _signal(model: str, action: str, confidence: float, regime: str = "ranging") -> dict:
    return {
        "model": model,
        "action": action,
        "confidence": confidence,
        "volume": 0.5,
        "context": {"regime": regime},
    }


def test_decision_signal_follows_regime_fallback() -> None:
    # All four vote BUY but disagree on regime: the gate says HOLD and no model
    # voted HOLD, so the representative is a BUY. Downstream must still see HOLD.
    cfg = {
        "regime_agreement": {"required": True, "min_agreement_ratio": 0.75, "fallback_action": "HOLD"},
        "voting": {"thresholds": {"buy": 0.2, "sell": -0.2}, "default_weights": {}},
    }
    signals = [
        _signal("gpt", "BUY", 0.9, "trending_up"),
        _signal("claude", "BUY", 0.8, "ranging"),
        _signal("gemini", "BUY", 0.7, "volatile"),
        _signal("deepseek", "BUY", 0.6, "trending_down"),
    ]
    fused = fuse_signals(signals, cfg)
    assert fused["representative_signal"]["action"] == "BUY"

    decision = decision_signal(fused)
    assert decision["action"] == "HOLD"
    assert decision["volume"] == 0.0
    assert decision["fusion_override"] == {"from": "BUY", "to": "HOLD"}


def test_decision_signal_follows_split_vote() -> None:
    cfg = {"voting": {"thresholds": {"buy": 0.35, "sell": -0.35}, "default_weights": {}}}
    signals = [
        _signal("gpt", "BUY", 0.9),
        _signal("claude", "BUY", 0.6),
        _signal("gemini", "SELL", 0.7),
        _signal("deepseek", "SELL", 0.6),
    ]
    fused = fuse_signals(signals, cfg)
    assert fused["action"] == "HOLD"
    assert decision_signal(fused)["action"] == "HOLD"


def test_decision_signal_keeps_agreeing_representative() -> None:
    cfg = {"voting": {"thresholds": {"buy": 0.2, "sell": -0.2}, "default_weights": {}}}
    signals = [_signal("gpt", "SELL", 0.8), _signal("claude", "SELL", 0.6)]
    fused = fuse_signals(signals, cfg)
    decision = decision_signal(fused)
    assert decision["action"] == "SELL"
    assert decision["volume"] == 0.5
    assert "fusion_override" not in decision
    # The fused result itself is not mutated.
    assert fused["representative_signal"] is not decision


def test_decision_signal_without_fusion_is_hold() -> None:
    assert decision_signal({})["action"] == "HOLD"
