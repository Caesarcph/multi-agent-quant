"""Safety behaviour of scripts/run_loop.py: dry run by default and risk-based sizing input."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def run_loop():
    spec = importlib.util.spec_from_file_location("run_loop", REPO_ROOT / "scripts" / "run_loop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def captured(run_loop, monkeypatch):
    calls = []

    def fake_execute_signal(signal, execution_cfg, mt5_cfg, positions=None):
        calls.append({"signal": signal, "cfg": execution_cfg})
        return {"status": "captured"}

    monkeypatch.setattr(run_loop, "execute_signal", fake_execute_signal)
    return calls


DECISION = {
    "decision": "EXECUTE",
    "order": {"symbol": "EURUSD", "action": "BUY", "volume": 5.0, "sl_price": 1.08, "tp_price": 1.10},
}


def _execute(run_loop, **kwargs):
    return run_loop.execute_trade(
        trading_decision=DECISION,
        config_dir=REPO_ROOT / "config",
        mt5_cfg={},
        positions=[],
        logger=logging.getLogger("test"),
        **kwargs,
    )


def test_dry_run_by_default(run_loop, captured):
    _execute(run_loop)
    assert captured[0]["cfg"]["dry_run"] is True


def test_live_flag_sends_orders(run_loop, captured):
    _execute(run_loop, live=True)
    assert captured[0]["cfg"]["dry_run"] is False


def test_default_config_sizes_by_risk(run_loop, captured):
    _execute(run_loop, risk_fraction=0.01)
    cfg, signal = captured[0]["cfg"], captured[0]["signal"]
    assert cfg["use_signal_volume"] is False
    assert cfg["use_risk_sizing"] is True
    assert signal["risk"]["risk_fraction_of_initial"] == 0.01
    assert signal["sl_price"] == 1.08


def test_skip_decision_never_executes(run_loop, captured):
    result = run_loop.execute_trade(
        trading_decision={"decision": "SKIP"},
        config_dir=REPO_ROOT / "config",
        mt5_cfg={},
        positions=[],
        logger=logging.getLogger("test"),
    )
    assert result["status"] == "NOT_EXECUTED"
    assert captured == []


@pytest.mark.parametrize(
    ("signal_risk", "per_trade", "expected"),
    [
        ({"risk_fraction_of_initial": 0.005}, 0.01, 0.005),  # throttled by guards -> keep
        ({"risk_fraction_of_initial": 0.5}, 0.01, 0.01),     # LLM asks for 50% -> capped
        ({}, 0.01, 0.01),                                     # missing -> per-trade budget
        ({"risk_fraction_of_initial": "bad"}, 0.01, 0.01),
        ({"risk_fraction_of_initial": 0.02}, None, 0.02),
    ],
)
def test_order_risk_fraction(run_loop, signal_risk, per_trade, expected):
    risk_cfg = {"trade_level": {"per_trade_risk": per_trade}}
    assert run_loop.order_risk_fraction({"risk": signal_risk}, risk_cfg) == expected


def test_shipped_risk_config_enforces_guards():
    import yaml

    risk_cfg = yaml.safe_load((REPO_ROOT / "config" / "risk_config.yaml").read_text())
    assert risk_cfg["enforcement"]["enabled"] is True
    assert risk_cfg["account_level"]["max_drawdown"] == 0.5
