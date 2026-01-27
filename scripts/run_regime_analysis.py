"""Run DeepSeek regime analysis for a symbol."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from deepseek.client import DeepSeekClient, load_deepseek_config
from deepseek.regime_analyst import DeepSeekRegimeAnalyst, build_multi_tf_data
from tools.config_loader import load_yaml


repo_root = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="Symbol to analyze")
    parser.add_argument("--force", action="store_true", help="Run even if disabled in config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_cfg = load_yaml(repo_root / "config" / "tool_config.yaml")
    mt5_cfg = tool_cfg.get("mt5", {})
    deepseek_cfg = load_deepseek_config(repo_root / "config")

    auxiliary_cfg = deepseek_cfg.get("auxiliary", {}) if deepseek_cfg else {}
    regime_cfg = auxiliary_cfg.get("regime_analysis", {}) if auxiliary_cfg else {}
    if not regime_cfg.get("enabled", False) and not args.force:
        raise SystemExit("regime_analysis is disabled in config/deepseek_config.yaml")

    client = DeepSeekClient(deepseek_cfg, agent_name="deepseek_aux")
    analyst = DeepSeekRegimeAnalyst(client)

    multi_tf_data = build_multi_tf_data(args.symbol, mt5_cfg, regime_cfg)
    result = analyst.analyze_regime(args.symbol, multi_tf_data)

    report_dir = repo_root / "reports" / "research"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"regime_{args.symbol}.json"
    payload = {
        "symbol": args.symbol,
        "multi_timeframe_data": multi_tf_data,
        "deepseek_result": result,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
