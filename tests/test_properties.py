"""Property-based tests for the invariants that protect the account."""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from execution.mt5_executor import _clamp_volume
from fusion.fusion_engine import decision_signal, fuse_signals
from risk.position_sizer import calculate_volume, size_order

EURUSD = {"trade_tick_size": 0.00001, "digits": 5, "point": 0.00001}

balances = st.floats(min_value=100.0, max_value=1_000_000.0)
risk_fractions = st.floats(min_value=0.0005, max_value=0.02)
stop_pips = st.floats(min_value=1.0, max_value=1000.0)
tick_values = st.floats(min_value=0.01, max_value=100.0)


@given(balance=balances, risk=risk_fractions, stop=stop_pips, tick_value=tick_values)
def test_sizing_never_exceeds_the_risk_budget(balance, risk, stop, tick_value) -> None:
    signal = {"risk": {"risk_fraction_of_initial": risk, "stop_loss_pips": stop}}
    symbol = {**EURUSD, "trade_tick_value": tick_value}
    volume, _ = calculate_volume(signal, symbol, {"balance": balance}, default_volume=0.01)
    pip_value = tick_value * 10  # value of one pip (10 points) for one lot
    assert volume * stop * pip_value <= balance * risk * (1 + 1e-9)


@given(
    balance=balances,
    risk=risk_fractions,
    stop=stop_pips,
    requested=st.one_of(st.none(), st.floats(min_value=-5.0, max_value=500.0)),
)
def test_requested_volume_can_only_shrink(balance, risk, stop, requested) -> None:
    signal = {
        "volume": requested,
        "risk": {"risk_fraction_of_initial": risk, "stop_loss_pips": stop},
    }
    symbol = {**EURUSD, "trade_tick_value": 1.0}
    risk_based, _ = calculate_volume(signal, symbol, {"balance": balance}, 0.01)
    volume, _ = size_order(signal, symbol, {"balance": balance}, 0.01)
    assert volume <= risk_based
    if requested is not None and 0 < requested < risk_based:
        assert volume == requested


@st.composite
def symbol_limits(draw):
    step = draw(st.sampled_from([0.01, 0.1, 1.0, 0.05]))
    min_steps = draw(st.integers(min_value=1, max_value=10))
    max_steps = draw(st.integers(min_value=min_steps, max_value=100_000))
    off_grid = draw(st.floats(min_value=0.0, max_value=0.99))  # volume_max need not be on grid
    return {
        "volume_step": step,
        "volume_min": min_steps * step,
        "volume_max": (max_steps + off_grid) * step,
    }


@given(limits=symbol_limits(), volume=st.floats(min_value=0.0, max_value=1e6))
@settings(max_examples=300)
def test_clamped_volume_is_valid_and_never_rounds_up(limits, volume) -> None:
    result = _clamp_volume(volume, limits)
    step, low, high = limits["volume_step"], limits["volume_min"], limits["volume_max"]

    assert low - 1e-9 <= result <= high + 1e-9
    steps = result / step
    assert math.isclose(steps, round(steps), abs_tol=1e-6)  # on the grid
    assert result <= max(volume, low) + 1e-9  # only volume_min may push it up


actions = st.sampled_from(["BUY", "SELL", "HOLD"])
regimes = st.sampled_from(["trending_up", "trending_down", "ranging", "volatile"])
model_signals = st.lists(
    st.tuples(
        st.sampled_from(["gpt", "claude", "gemini", "deepseek"]),
        actions,
        regimes,
        st.floats(min_value=0.0, max_value=1.0),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda t: t[0],
)


@given(signals=model_signals, required=st.booleans(), ratio=st.sampled_from([0.5, 0.75, 1.0]))
def test_executed_action_always_equals_the_fused_decision(signals, required, ratio) -> None:
    cfg = {
        "regime_agreement": {
            "required": required,
            "min_agreement_ratio": ratio,
            "fallback_action": "HOLD",
        },
        "voting": {"thresholds": {"buy": 0.35, "sell": -0.35}, "default_weights": {}},
    }
    payload = [
        {"model": m, "action": a, "context": {"regime": r}, "confidence": c, "volume": 1.0}
        for m, a, r, c in signals
    ]
    fused = fuse_signals(payload, cfg)
    decision = decision_signal(fused)

    assert decision["action"] == fused["action"]
    assert -1.0 <= fused["directional_score"] <= 1.0
    if "fusion_override" in decision:
        # The representative pointed elsewhere; the override must also drop its volume.
        assert decision["action"] == "HOLD"
        assert decision["volume"] == 0.0
