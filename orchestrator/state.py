"""Orchestrator state container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OrchestratorState:
    cycle_id: int = 0
    account_info: dict[str, Any] | None = None
