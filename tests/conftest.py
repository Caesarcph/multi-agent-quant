"""Shared test fixtures.

MetaTrader5 only installs on Windows. When it is missing (Linux/macOS, CI), a fake
module is registered so project modules can be imported. Tests that exercise MT5
behaviour use the ``fake_mt5`` fixture, which patches the module reference inside
each project module -- so they behave the same on Windows, where the real package
is importable but no terminal is running.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes.mt5 import FakeMT5

try:  # pragma: no cover - depends on the platform
    import MetaTrader5  # noqa: F401
except ImportError:  # pragma: no cover
    sys.modules["MetaTrader5"] = FakeMT5()

# Every project module that does `import MetaTrader5 as mt5`.
MT5_MODULES = (
    "data.trade_history",
    "execution.mt5_executor",
    "tools.mt5_connection",
    "tools.mt5_tools",
    "tools.tool_registry",
)


@pytest.fixture
def fake_mt5(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMT5]:
    """A fresh fake terminal wired into every module that talks to MT5."""
    fake = FakeMT5()
    for name in MT5_MODULES:
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "mt5", fake)
    yield fake


@pytest.fixture
def mt5_cfg(tmp_path: Path) -> dict[str, object]:
    """MT5 settings pointing at an existing (dummy) terminal executable."""
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"")
    return {"terminal_path": str(terminal), "initialize_timeout_seconds": 1}


@pytest.fixture(autouse=True)
def _no_proxy_for_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake LLM servers listen on 127.0.0.1; never route those requests via a proxy."""
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key, "")
        monkeypatch.setenv(key, ",".join(filter(None, [current, "127.0.0.1", "localhost"])))
