"""Generate DeepSeek daily summary report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.reporting import (
    aggregate_cycles,
    load_cycles,
    load_daily_state,
    today_utc,
)
from deepseek.client import DeepSeekClient, load_deepseek_config
from deepseek.daily_analyst import DeepSeekDailyAnalyst
from tools.config_loader import load_yaml


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="UTC date in YYYYMMDD format (default: today)")
    parser.add_argument("--limit", type=int, help="Max cycles to include")
    parser.add_argument("--force", action="store_true", help="Run even if disabled in config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    deepseek_cfg = load_deepseek_config(repo_root / "config")

    auxiliary_cfg = deepseek_cfg.get("auxiliary", {}) if deepseek_cfg else {}
    daily_cfg = auxiliary_cfg.get("daily_summary", {}) if auxiliary_cfg else {}
    if not daily_cfg.get("enabled", False) and not args.force:
        raise SystemExit("daily_summary is disabled in config/deepseek_config.yaml")

    logging_cfg = tool_cfg.get("logging", {})
    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")

    report_cfg = tool_cfg.get("report", {})
    report_dir = repo_root / report_cfg.get("directory", "reports/daily")

    report_date = args.date or today_utc()

    cycles = load_cycles(cycle_dir, limit=args.limit)
    if report_date:
        cycles = [cycle for cycle in cycles if _matches_date(cycle, report_date)]

    summary = aggregate_cycles(cycles)

    daily_state = None
    daily_cfg = tool_cfg.get("daily_state", {})
    if daily_cfg.get("enabled", False):
        daily_state = load_daily_state(repo_root / daily_cfg.get("path", "logs/daily_state.json"))

    account_state = None
    account_cfg = tool_cfg.get("account_state", {})
    if account_cfg.get("enabled", False):
        path = repo_root / account_cfg.get("path", "logs/account_state.json")
        if path.exists():
            account_state = json.loads(path.read_text(encoding="utf-8"))

    client = DeepSeekClient(deepseek_cfg, agent_name="deepseek_aux")
    analyst = DeepSeekDailyAnalyst(client)
    result = analyst.generate_daily_report(summary, account_state, daily_state)

    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"{report_date}_deepseek_summary.json"
    payload = {
        "date": report_date,
        "summary": summary,
        "daily_state": daily_state,
        "account_state": account_state,
        "deepseek_result": result,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output_path)


def _matches_date(cycle: dict, report_date: str) -> bool:
    timestamp = cycle.get("snapshot_meta", {}).get("timestamp")
    if not timestamp:
        return False
    return timestamp.startswith(
        f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:8]}"
    )


if __name__ == "__main__":
    main()
