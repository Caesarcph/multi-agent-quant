"""maq_core.logging: one pipeline for structlog and stdlib loggers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from maq_core.logging import configure_logging, get_logger, log_context


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_stdlib_and_structlog_share_files_and_context(tmp_path: Path, restore_root_logger) -> None:
    configure_logging(tmp_path, level="INFO", console=False)

    with log_context(cycle=7, symbol="EURUSD"):
        logging.getLogger("legacy.module").info("research done for %s", "EURUSD")
        get_logger("new.module").info("order_sent", volume=0.5)
    logging.getLogger("legacy.module").debug("filtered out")

    records = read_jsonl(tmp_path / "orchestrator.jsonl")
    assert [r["event"] for r in records] == ["research done for EURUSD", "order_sent"]
    assert all(r["cycle"] == 7 and r["symbol"] == "EURUSD" for r in records)
    assert records[0]["logger"] == "legacy.module"
    assert records[1]["volume"] == 0.5
    assert records[1]["level"] == "info"
    assert "research done for EURUSD" in (tmp_path / "orchestrator.log").read_text(encoding="utf-8")


def test_exceptions_are_serialised(tmp_path: Path, restore_root_logger) -> None:
    configure_logging(tmp_path, console=False)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("failed")
    (record,) = read_jsonl(tmp_path / "orchestrator.jsonl")
    assert "ValueError: boom" in record["exception"]


def test_reconfiguring_does_not_duplicate_handlers(tmp_path: Path, restore_root_logger) -> None:
    configure_logging(tmp_path, console=False)
    configure_logging(tmp_path, console=False)
    logging.getLogger("x").warning("once")
    assert len(read_jsonl(tmp_path / "orchestrator.jsonl")) == 1
