"""Schema validation sanity check."""
from __future__ import annotations

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from agents.prompt_loader import load_output_schema
from agents.schema_validator import validate_signal


def test_signal_schema_validation() -> None:
    schema = load_output_schema()
    signal = {
        "model": "deepseek",
        "timestamp": "2026-01-21T00:00:00Z",
        "symbol": "EURUSD",
        "timeframe": "M1",
        "action": "HOLD",
        "volume": 0.0,
        "confidence": 0.5,
        "horizon": "intra",
        "entry": {"type": "market", "price": None},
        "risk": {
            "stop_loss_pips": 10,
            "take_profit_pips": 20,
            "risk_fraction_of_initial": 0.01,
        },
        "analysis": {
            "basis": "technical",
            "time_horizon": "short_term",
            "setup": "Test setup description.",
            "sl_tp_rationale": "SL below support, TP at resistance.",
        },
        "context": {
            "regime": "ranging",
            "key_levels": {"support": [1.0], "resistance": [1.2]},
        },
        "reason_codes": ["TEST"],
        "invalidation": ["TEST_INVALID"],
        "ttl_minutes": 60,
    }
    validate_signal(signal, schema)
