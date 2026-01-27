"""Run a multi-symbol scan once."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from data.cycle_store import save_cycle
from orchestrator.scan import resolve_scan_symbols, run_scan_once
from tools.config_loader import load_yaml
from tools.logging_setup import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="Comma-separated symbols to scan")
    parser.add_argument("--timeframe", help="Override snapshot timeframe (e.g. M1)")
    parser.add_argument("--bars", type=int, help="Override snapshot bar count")
    parser.add_argument(
        "--agents",
        help="Comma-separated agent names to include (e.g. deepseek)",
    )
    parser.add_argument("--print-json", action="store_true", help="Print full JSON output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_dir = repo_root / "config"

    tool_cfg = load_yaml(config_dir / "tool_config.yaml")
    logging_cfg = tool_cfg.get("logging", {})
    log_dir = repo_root / logging_cfg.get("directory", "logs")
    setup_logging(log_dir, logging_cfg.get("level", "INFO"))

    agent_list = None
    if args.agents:
        agent_list = [name.strip() for name in args.agents.split(",") if name.strip()]

    symbols = None
    if args.symbols:
        symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]

    resolved_symbols = symbols or resolve_scan_symbols(config_dir)
    logging.getLogger(__name__).info("Scanning %d symbols", len(resolved_symbols))

    scan_result = run_scan_once(
        config_dir=config_dir,
        symbols=resolved_symbols,
        timeframe=args.timeframe,
        bars=args.bars,
        include_agents=agent_list,
    )

    cycle_dir = logging_cfg.get("cycle_dir", "logs/cycles")
    output_dir = repo_root / cycle_dir
    for result in scan_result["results"]:
        path = save_cycle(result, output_dir)
        logging.getLogger(__name__).info(
            "Saved %s cycle to %s",
            result.get("snapshot_meta", {}).get("symbol"),
            path,
        )

    summary = {
        "timestamp": scan_result["timestamp"],
        "symbol_count": len(scan_result["symbols"]),
        "success_count": len(scan_result["results"]),
        "error_count": len(scan_result["errors"]),
    }

    if args.print_json:
        print(json.dumps(scan_result, indent=2))
    else:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
