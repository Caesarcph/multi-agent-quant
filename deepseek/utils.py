"""Helpers for parsing DeepSeek outputs."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from agents.json_utils import extract_json_object


def parse_json_output(raw_output: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        return extract_json_object(raw_output), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
