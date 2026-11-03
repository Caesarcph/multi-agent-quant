"""Helpers for parsing DeepSeek outputs."""

from __future__ import annotations

from typing import Any

from agents.json_utils import extract_json_object


def parse_json_output(raw_output: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return extract_json_object(raw_output), None
    except Exception as exc:
        return None, str(exc)
