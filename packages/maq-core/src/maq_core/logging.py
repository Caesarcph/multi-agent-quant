"""Structured logging shared by new and legacy code.

``configure_logging`` routes both structlog loggers and plain ``logging.getLogger``
calls through one pipeline:

- console: human-readable, coloured when attached to a terminal;
- ``<name>.log``: the same human-readable lines, rotated;
- ``<name>.jsonl``: one JSON object per line, rotated, for analysis.

Values bound with ``log_context(cycle=3, symbol="EURUSD")`` are attached to every
line logged inside the block, by any logger.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

_HANDLER_MARK = "_maq_core_handler"

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
]


def _formatter(renderer: Any, *extra: Any) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *extra, renderer],
    )


def configure_logging(
    log_dir: Path | str | None = None,
    level: str | int = "INFO",
    name: str = "orchestrator",
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure logging for the process. Safe to call more than once."""
    if isinstance(level, str):
        level_value = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    else:
        level_value = level

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = []
    if console:
        stream = logging.StreamHandler(sys.stderr)
        colors = bool(getattr(sys.stderr, "isatty", lambda: False)())
        stream.setFormatter(_formatter(structlog.dev.ConsoleRenderer(colors=colors)))
        handlers.append(stream)

    if log_dir is not None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        text = logging.handlers.RotatingFileHandler(
            directory / f"{name}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        text.setFormatter(_formatter(structlog.dev.ConsoleRenderer(colors=False)))
        handlers.append(text)

        jsonl = logging.handlers.RotatingFileHandler(
            directory / f"{name}.jsonl",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        jsonl.setFormatter(
            _formatter(structlog.processors.JSONRenderer(), structlog.processors.format_exc_info)
        )
        handlers.append(jsonl)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()
    for handler in handlers:
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    root.setLevel(level_value)


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """A structlog logger: ``get_logger(__name__).info("order_sent", volume=0.1)``."""
    return structlog.stdlib.get_logger(name, **initial_values)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Attach key/values to every log line emitted inside the block (any logger)."""
    with structlog.contextvars.bound_contextvars(**values):
        yield


def bind_log_context(**values: Any) -> None:
    """Like ``log_context`` but without a block: values stay until unbound."""
    structlog.contextvars.bind_contextvars(**values)


def unbind_log_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*keys)
