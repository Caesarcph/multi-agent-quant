"""Multi-symbol scan orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data.portfolio_state import get_open_positions
from orchestrator.cycle import run_once
from tools import mt5_connection, mt5_tools
from tools.config_loader import load_yaml


def _fetch_symbols_and_positions(
    terminal_path: str,
    timeout_seconds: int,
    include_positions: bool,
) -> tuple[List[str], List[Dict[str, Any]]]:
    mt5_connection.initialize(terminal_path=terminal_path, timeout_seconds=timeout_seconds)
    try:
        symbols = [info.get("name") for info in mt5_tools.get_symbols()]
        symbols = [name for name in symbols if name]
        positions = get_open_positions() if include_positions else []
    finally:
        mt5_connection.shutdown()
    return symbols, positions


def resolve_scan_symbols(
    config_dir: Path,
    override_symbols: Optional[List[str]] = None,
) -> List[str]:
    tool_cfg = load_yaml(config_dir / "tool_config.yaml")
    mt5_cfg = tool_cfg.get("mt5", {})
    snapshot_cfg = tool_cfg.get("snapshot", {})
    scan_cfg = tool_cfg.get("scan", {})

    if override_symbols:
        symbols = [symbol.strip() for symbol in override_symbols if symbol.strip()]
    else:
        symbols = scan_cfg.get("symbols") or snapshot_cfg.get("symbol_universe") or []

    include_open_positions = bool(scan_cfg.get("include_open_positions", True))
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required in config/tool_config.yaml")

    timeout_seconds = mt5_cfg.get("initialize_timeout_seconds", 30)
    positions: List[Dict[str, Any]] = []

    if not symbols:
        symbols, positions = _fetch_symbols_and_positions(
            terminal_path, timeout_seconds, include_open_positions
        )
    elif include_open_positions:
        _, positions = _fetch_symbols_and_positions(
            terminal_path, timeout_seconds, include_open_positions
        )

    if include_open_positions and positions:
        position_symbols = {pos.get("symbol") for pos in positions if pos.get("symbol")}
        symbols = list(dict.fromkeys(list(position_symbols) + symbols))

    symbol_limit = scan_cfg.get("symbol_limit", snapshot_cfg.get("symbol_limit"))
    if symbol_limit is not None:
        symbols = symbols[: int(symbol_limit)]

    return symbols


def run_scan_once(
    config_dir: Path,
    symbols: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    bars: Optional[int] = None,
    include_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    symbols = symbols or resolve_scan_symbols(config_dir)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for symbol in symbols:
        try:
            result = run_once(
                config_dir=config_dir,
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                include_agents=include_agents,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": symbol, "error": str(exc)})

    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbols": symbols,
        "results": results,
        "errors": errors,
    }
