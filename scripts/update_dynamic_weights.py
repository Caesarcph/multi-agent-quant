"""Update dynamic weights based on recent signal outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.reporting import load_cycles
from fusion.dynamic_weighting import update_dynamic_weights
from tools.config_loader import load_yaml


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--force", action="store_true", help="Run even if disabled in config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    fusion_cfg = load_yaml(repo_root / "config" / "fusion_config.yaml")

    dynamic_cfg = fusion_cfg.get("dynamic_weighting", {}) or {}
    if not dynamic_cfg.get("enabled", False) and not args.force:
        raise SystemExit("dynamic_weighting is disabled in config/fusion_config.yaml")

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")

    cycles = load_cycles(cycle_dir, limit=args.limit)
    output_path = repo_root / dynamic_cfg.get("weights_path", "logs/dynamic_weights.json")
    default_weights = fusion_cfg.get("voting", {}).get("default_weights", {})
    mt5_cfg = tool_cfg.get("mt5", {})

    result = update_dynamic_weights(
        cycles=cycles,
        output_path=output_path,
        mt5_cfg=mt5_cfg,
        config=dynamic_cfg,
        default_weights=default_weights,
    )
    payload = {"weights": result.weights, "stats": result.stats, "path": str(output_path)}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
