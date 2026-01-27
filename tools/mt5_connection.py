"""MetaTrader 5 terminal lifecycle."""
from __future__ import annotations

from pathlib import Path

import MetaTrader5 as mt5


def initialize(terminal_path: str, timeout_seconds: int = 30) -> None:
    path = Path(terminal_path)
    if not path.exists():
        raise FileNotFoundError(f"MT5 terminal not found: {path}")
    timeout_ms = int(timeout_seconds * 1000)
    if not mt5.initialize(path=str(path), timeout=timeout_ms):
        code, message = mt5.last_error()
        raise RuntimeError(f"MT5 initialize failed: {code} {message}")


def shutdown() -> None:
    mt5.shutdown()
