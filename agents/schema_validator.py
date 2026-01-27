"""JSON schema validation for agent signals."""
from __future__ import annotations

from typing import Any, Dict

from jsonschema import Draft7Validator


# Fields where LLMs commonly output negative values but mean the absolute value
_ABS_FIELDS = {"volume", "stop_loss_pips", "take_profit_pips", "ttl_minutes"}


def _sanitize_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Fix common LLM output mistakes before schema validation."""
    out = dict(signal)

    # Top-level fields
    for key in ("volume", "ttl_minutes"):
        if key in out and isinstance(out[key], (int, float)):
            out[key] = abs(out[key])

    # Nested risk fields
    risk = out.get("risk")
    if isinstance(risk, dict):
        risk = dict(risk)
        for key in ("stop_loss_pips", "take_profit_pips", "risk_fraction_of_initial"):
            if key in risk and isinstance(risk[key], (int, float)):
                risk[key] = abs(risk[key])
        out["risk"] = risk

    return out


def validate_signal(signal: Dict[str, Any], schema: Dict[str, Any]) -> None:
    sanitized = _sanitize_signal(signal)
    # Mutate original so callers get the cleaned values
    signal.update(sanitized)

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(signal), key=lambda err: list(err.path))
    if not errors:
        return
    messages = []
    for err in errors:
        path = ".".join([str(part) for part in err.path]) or "<root>"
        messages.append(f"{path}: {err.message}")
    raise ValueError("Schema validation failed: " + "; ".join(messages))
