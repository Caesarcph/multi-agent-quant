"""JSON parsing utilities for model output."""
from __future__ import annotations

import json
import re


def _find_json_slice(text: str) -> str:
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == "\"":
                in_string = False
            continue

        if char == "\"":
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    raise ValueError("No complete JSON object found in model output.")


def _normalize_json_text(raw: str) -> str:
    cleaned = raw.strip()
    cleaned = re.sub(r"(?m)^\s*//.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*#.*$", "", cleaned)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    cleaned = re.sub(r"\bNone\b", "null", cleaned)
    cleaned = re.sub(r"\bTrue\b", "true", cleaned)
    cleaned = re.sub(r"\bFalse\b", "false", cleaned)
    cleaned = re.sub(r"\bNaN\b", "null", cleaned)
    cleaned = re.sub(r"\b-?Infinity\b", "null", cleaned)
    cleaned = _strip_non_ascii_outside_strings(cleaned)
    return cleaned


def _strip_non_ascii_outside_strings(text: str) -> str:
    output: list[str] = []
    in_string = False
    escape = False

    for char in text:
        if in_string:
            output.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == "\"":
                in_string = False
            continue

        if char == "\"":
            in_string = True
            output.append(char)
            continue

        if ord(char) < 128:
            output.append(char)

    return "".join(output)


def extract_json_object(text: str) -> dict:
    raw = _find_json_slice(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        normalized = _normalize_json_text(raw)
        try:
            return json.loads(normalized)
        except json.JSONDecodeError as exc:
            snippet = normalized[:300].replace("\n", "\\n")
            raise ValueError(f"Failed to parse JSON output. Snippet: {snippet}") from exc
