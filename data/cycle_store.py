"""Persist cycle outputs to disk."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def save_cycle(result: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    symbol = result.get("snapshot_meta", {}).get("symbol", "UNKNOWN")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{stamp}_{symbol}.json"
    path = output_dir / filename
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path
