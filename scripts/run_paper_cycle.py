"""Run a cycle and apply paper trading simulation."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from orchestrator.cycle import run_once
from tools.config_loader import load_yaml
from tools.logging_setup import setup_logging
from validation.paper_trader import PaperTrader


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

    validation_cfg = load_yaml(config_dir / "validation_config.yaml")
    paper_cfg = validation_cfg.get("paper_trading", {})
    state_path = repo_root / paper_cfg.get("state_path", "logs/paper_state.json")
    paper_trader = PaperTrader(state_path, paper_cfg)
    paper_result = paper_trader.apply_signal(result["final_signal"], tool_cfg.get("mt5", {}))

    logging.getLogger(__name__).info("Paper trading status=%s", paper_result.status)
    output = {
        "final_signal": result["final_signal"],
        "paper_result": {
            "status": paper_result.status,
            "notes": paper_result.notes,
            "closed_trades": paper_result.closed_trades,
            "equity": paper_result.state.get("equity"),
            "balance": paper_result.state.get("balance"),
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
