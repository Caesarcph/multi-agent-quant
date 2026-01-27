"""Exposure and portfolio guards."""
from __future__ import annotations

from typing import Any, Dict, List


def check_exposure_limits(
    signal: Dict[str, Any],
    positions: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> List[str]:
    notes: List[str] = []
    action = signal.get("action")
    if action not in {"BUY", "SELL"}:
        return notes

    max_positions = config.get("max_concurrent_positions")
    if max_positions is not None and len(positions) >= int(max_positions):
        notes.append("max_positions_reached")

    max_symbol_positions = config.get("max_positions_per_symbol")
    if max_symbol_positions is not None:
        symbol = signal.get("symbol")
        symbol_count = sum(1 for pos in positions if pos.get("symbol") == symbol)
        if symbol_count >= int(max_symbol_positions):
            notes.append("max_symbol_positions_reached")

    max_same_direction = config.get("max_same_direction")
    if max_same_direction is not None:
        direction_positions = [
            pos for pos in positions if _direction_from_position(pos) == action
        ]
        if len(direction_positions) >= int(max_same_direction):
            notes.append("max_same_direction_reached")

    return notes


def _direction_from_position(position: Dict[str, Any]) -> str:
    pos_type = position.get("type")
    if pos_type is None:
        return "UNKNOWN"
    return "BUY" if int(pos_type) == 0 else "SELL"
