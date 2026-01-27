"""Run MT5-backed backtest using cycle logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.reporting import load_cycles
from tools.config_loader import load_yaml
from validation.mt5_backtest import run_mt5_backtest


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--use-all-signals", action="store_true", help="Use all agent signals")
    parser.add_argument("--models", help="Comma-separated model names to include")
    parser.add_argument("--symbols", help="Comma-separated symbols to include")
    parser.add_argument("--output", help="Output path for report JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    validation_cfg = load_yaml(repo_root / "config" / "validation_config.yaml")
    backtest_cfg = validation_cfg.get("mt5_backtest", {})

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")
    cycles = load_cycles(cycle_dir, limit=args.limit)

    config = dict(backtest_cfg)
    config["use_final_signal"] = not args.use_all_signals
    if args.models:
        config["models"] = [name.strip() for name in args.models.split(",") if name.strip()]
    if args.symbols:
        config["symbols"] = [name.strip() for name in args.symbols.split(",") if name.strip()]

    result = run_mt5_backtest(cycles, tool_cfg.get("mt5", {}), config)

    output_path = (
        Path(args.output)
        if args.output
        else repo_root / "reports" / "validation" / "mt5_backtest.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
