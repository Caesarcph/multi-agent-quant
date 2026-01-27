"""Orchestrator entrypoint and runtime wiring."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from orchestrator.state import OrchestratorState
from tools.config_loader import load_yaml
from tools.logging_setup import setup_logging
from tools import mt5_connection, mt5_tools


def main() -> None:
    tool_config = load_yaml(repo_root / "config" / "tool_config.yaml")

    logging_cfg = tool_config.get("logging", {})
    log_dir = repo_root / logging_cfg.get("directory", "logs")
    setup_logging(log_dir, logging_cfg.get("level", "INFO"))

    mt5_cfg = tool_config.get("mt5", {})
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required in config/tool_config.yaml")

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        state = OrchestratorState()
        state.account_info = mt5_tools.get_account_info()
        logging.getLogger(__name__).info("MT5 account info: %s", state.account_info)
    finally:
        mt5_connection.shutdown()


if __name__ == "__main__":
    main()
