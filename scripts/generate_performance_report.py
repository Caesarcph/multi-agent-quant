"""Generate model performance report from cycle logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.reporting import load_cycles
from tools.config_loader import load_yaml
from validation.model_performance import evaluate_model_performance


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--output", help="Output path for the report JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    validation_cfg = load_yaml(repo_root / "config" / "validation_config.yaml")
    perf_cfg = validation_cfg.get("performance_tracking", {})

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")
    cycles = load_cycles(cycle_dir, limit=args.limit)

    mt5_cfg = tool_cfg.get("mt5", {})
    report = evaluate_model_performance(cycles, mt5_cfg, perf_cfg)

    output_path = (
        Path(args.output)
        if args.output
        else repo_root / "reports" / "performance" / "model_performance.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
