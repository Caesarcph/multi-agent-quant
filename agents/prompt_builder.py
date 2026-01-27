"""Prompt construction helpers."""
from __future__ import annotations

import json
from typing import Any, Dict


def build_prompt(snapshot: Dict[str, Any], schema: Dict[str, Any]) -> str:
    snapshot_json = json.dumps(snapshot, indent=2, sort_keys=True)
    schema_json = json.dumps(schema, indent=2, sort_keys=True)
    return (
        "Market snapshot (JSON):\n"
        f"{snapshot_json}\n\n"
        "Return a JSON object that matches this schema exactly:\n"
        f"{schema_json}\n"
    )
