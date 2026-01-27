"""Attribute MT5 deals to cycle signals."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.reporting import load_cycles
from tools import mt5_connection, mt5_tools
from tools.config_loader import load_yaml
from validation.trade_attribution import attribute_deals


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--days", type=int, default=1, help="Days of history to pull")
    parser.add_argument("--output", help="Output path for report JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    validation_cfg = load_yaml(repo_root / "config" / "validation_config.yaml")
    attrib_cfg = validation_cfg.get("trade_attribution", {})

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")
    cycles = load_cycles(cycle_dir, limit=args.limit)

    mt5_cfg = tool_cfg.get("mt5", {})
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required for attribution")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(args.days))

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        deals = mt5_tools.get_deals(start, end)
    finally:
        mt5_connection.shutdown()

    result = attribute_deals(cycles, deals, attrib_cfg)

    output_path = (
        Path(args.output)
        if args.output
        else repo_root / "reports" / "validation" / "trade_attribution.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": result.summary,
        "attributed": result.attributed,
        "unmatched": result.unmatched,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
