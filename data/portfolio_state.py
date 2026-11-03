"""Portfolio state derived from MT5."""

from __future__ import annotations

from typing import Any

from tools import mt5_tools


def get_open_positions() -> list[dict[str, Any]]:
    try:
        return mt5_tools.get_positions()
    except RuntimeError:
        return []


def get_open_orders() -> list[dict[str, Any]]:
    try:
        return mt5_tools.get_orders()
    except RuntimeError:
        return []
