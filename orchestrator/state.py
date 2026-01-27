"""Orchestrator state container."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class OrchestratorState:
    cycle_id: int = 0
    account_info: Optional[Dict[str, Any]] = None
