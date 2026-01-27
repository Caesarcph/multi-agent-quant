"""Run a single MT5 -> LLM -> fusion cycle."""
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
from orchestrator.cycle import run_once
from tools.config_loader import load_yaml
from tools.logging_setup import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Override snapshot symbol")
    parser.add_argument("--timeframe", help="Override snapshot timeframe (e.g. M1)")
    parser.add_argument("--bars", type=int, help="Override snapshot bar count")
    parser.add_argument(
        "--agents",
        help="Comma-separated agent names to include (e.g. deepseek)",
    )
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

    result = run_once(
        config_dir=config_dir,
        symbol=args.symbol,
        timeframe=args.timeframe,
        bars=args.bars,
        include_agents=agent_list,
    )

    logging.getLogger(__name__).info(
        "Final action=%s agreement=%.2f",
        result["fusion"]["action"],
        result["fusion"]["agreement_ratio"],
    )

    print(json.dumps(result, indent=2))
    cycle_dir = logging_cfg.get("cycle_dir", "logs/cycles")
    path = save_cycle(result, repo_root / cycle_dir)
    logging.getLogger(__name__).info("Cycle saved to %s", path)


if __name__ == "__main__":
    main()
