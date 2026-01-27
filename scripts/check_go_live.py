"""Evaluate go-live criteria from paper trading results."""
from __future__ import annotations

import json
from pathlib import Path

from tools.config_loader import load_yaml
from validation.go_live_checker import check_go_live
from validation.performance_tracker import compute_metrics, save_metrics


repo_root = Path(__file__).resolve().parents[1]


def main() -> None:
    validation_cfg = load_yaml(repo_root / "config" / "validation_config.yaml")
    paper_cfg = validation_cfg.get("paper_trading", {})
    state_path = repo_root / paper_cfg.get("state_path", "logs/paper_state.json")
    if not state_path.exists():
        raise SystemExit(f"Paper trading state not found: {state_path}")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    trades = state.get("trades", [])
    initial_balance = state.get("initial_balance")
    metrics = compute_metrics(trades, initial_balance=initial_balance)

    report_dir = repo_root / "reports" / "validation"
    report_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = report_dir / "paper_metrics.json"
    save_metrics(metrics_path, metrics)

    criteria = validation_cfg.get("go_live_criteria", {})
    result = check_go_live(metrics, criteria)
    output = {
        "metrics": metrics,
        "go_live": result,
        "metrics_path": str(metrics_path),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
