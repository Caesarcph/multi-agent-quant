"""Logging setup for the entry-point scripts (see maq_core.logging)."""

from __future__ import annotations

from pathlib import Path

from maq_core.logging import configure_logging


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Console + rotating ``orchestrator.log`` (text) and ``orchestrator.jsonl`` (JSON)."""
    configure_logging(log_dir, level=level, name="orchestrator")
