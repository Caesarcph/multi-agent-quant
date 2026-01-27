"""Prompt and schema loaders."""
from __future__ import annotations

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_system_prompt() -> str:
    path = PROMPTS_DIR / "system_prompt.md"
    return path.read_text(encoding="utf-8").strip()


def load_output_schema() -> dict:
    path = PROMPTS_DIR / "output_schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
