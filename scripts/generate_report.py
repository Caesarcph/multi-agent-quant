"""Generate a daily report from cycle logs."""
from __future__ import annotations

import argparse
from pathlib import Path

from data.reporting import (
    aggregate_cycles,
    build_report_payload,
    load_cycles,
    load_daily_state,
    render_markdown_report,
    save_json_report,
    save_report,
    today_utc,
)
from tools.config_loader import load_yaml


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="UTC date in YYYYMMDD format (default: today)")
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--format", help="Comma-separated formats: md,json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")

    report_cfg = tool_cfg.get("report", {})
    report_dir = repo_root / report_cfg.get("directory", "reports/daily")
    formats = report_cfg.get("formats", ["md"])

    report_date = args.date or today_utc()
    if args.format:
        formats = [fmt.strip() for fmt in args.format.split(",") if fmt.strip()]

    cycles = load_cycles(cycle_dir, limit=args.limit)
    if report_date:
        cycles = [cycle for cycle in cycles if _matches_date(cycle, report_date)]

    summary = aggregate_cycles(cycles)

    daily_cfg = tool_cfg.get("daily_state", {})
    daily_state = None
    if daily_cfg.get("enabled", False):
        daily_state = load_daily_state(repo_root / daily_cfg.get("path", "logs/daily_state.json"))

    outputs = []
    if "md" in formats:
        content = render_markdown_report(report_date, summary, daily_state)
        report_path = report_dir / f"{report_date}_report.md"
        save_report(report_path, content)
        outputs.append(report_path)
    if "json" in formats:
        payload = build_report_payload(report_date, summary, daily_state)
        json_path = report_dir / f"{report_date}_report.json"
        save_json_report(json_path, payload)
        outputs.append(json_path)

    for path in outputs:
        print(path)


def _matches_date(cycle: dict, report_date: str) -> bool:
    timestamp = cycle.get("snapshot_meta", {}).get("timestamp")
    if not timestamp:
        return False
    return timestamp.startswith(
        f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:8]}"
    )


if __name__ == "__main__":
    main()
