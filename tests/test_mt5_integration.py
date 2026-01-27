"""Integration tests against a live MT5 terminal.

Enable with MT5_INTEGRATION=1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from tools.config_loader import load_yaml
from tools import mt5_connection, mt5_tools


pytestmark = pytest.mark.skipif(
    os.getenv("MT5_INTEGRATION") != "1",
    reason="Set MT5_INTEGRATION=1 to run live MT5 tests.",
)


def test_mt5_basic_flow() -> None:
    tool_config = load_yaml(repo_root / "config" / "tool_config.yaml")
    mt5_cfg = tool_config.get("mt5", {})
    terminal_path = mt5_cfg.get("terminal_path")
    assert terminal_path

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        account = mt5_tools.get_account_info()
        assert account.get("login")

        symbols = mt5_tools.get_symbols()
        assert symbols

        symbol = mt5_cfg.get("default_symbol") or symbols[0]["name"]
        symbol_info = mt5_tools.get_symbol_info(symbol)
        assert symbol_info.get("name") == symbol

        rates = mt5_tools.get_rates(symbol, "M1", count=10)
        assert len(rates) > 0

        ticks = mt5_tools.get_ticks(symbol, count=10)
        assert len(ticks) > 0
    finally:
        mt5_connection.shutdown()
