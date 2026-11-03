"""Recovering a JSON object from free-form model output."""

from __future__ import annotations

import json

import pytest

from agents.json_utils import extract_json_object


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"action": "BUY"}', {"action": "BUY"}),
        ('Sure! ```json\n{"action": "SELL"}\n``` Hope that helps.', {"action": "SELL"}),
        ('{"a": 1, "b": [1, 2,],}', {"a": 1, "b": [1, 2]}),
        ('{"a": True, "b": None, "c": False}', {"a": True, "b": None, "c": False}),
        ('{\n  // a comment\n  "a": 1\n}', {"a": 1}),
        ('{"note": "levels {1.08} and }", "x": 2}', {"note": "levels {1.08} and }", "x": 2}),
        ('{"a": NaN, "b": Infinity, "c": -Infinity}', {"a": None, "b": None, "c": None}),
        # A full-width comma outside a string is junk; inside strings non-ASCII is kept.
        ('{"setup": "回踩支撑", "a": 1，}', {"setup": "回踩支撑", "a": 1}),  # noqa: RUF001
        ('{"a": {"b": {"c": 1}}} trailing {"ignored": true}', {"a": {"b": {"c": 1}}}),
    ],
)
def test_recovers_object(text: str, expected: dict) -> None:
    assert extract_json_object(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no json here", "No JSON object found"),
        ('{"a": 1', "No complete JSON object found"),
        ('{"a": [1, 2}', "Failed to parse JSON output"),
    ],
)
def test_unrecoverable_output_raises(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_json_object(text)


def test_nan_confidence_cannot_pass_schema_validation() -> None:
    from agents.prompt_loader import load_output_schema
    from agents.schema_validator import validate_signal
    from tests.factories import research_signal

    text = json.dumps(research_signal()).replace('"confidence": 0.7', '"confidence": NaN')
    with pytest.raises(ValueError):
        validate_signal(extract_json_object(text), load_output_schema())
