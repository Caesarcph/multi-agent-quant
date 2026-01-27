"""Quick MT5 smoke test with basic data fetches."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from tools.config_loader import load_yaml
from tools import mt5_connection, mt5_tools


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Symbol to query, e.g. EURUSD")
    parser.add_argument("--timeframe", default="M1", help="Timeframe like M1, H1")
    parser.add_argument("--count", type=int, default=20, help="Number of bars/ticks")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_config = load_yaml(repo_root / "config" / "tool_config.yaml")
    mt5_cfg = tool_config.get("mt5", {})
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required in config/tool_config.yaml")

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        account = mt5_tools.get_account_info()
        symbols = mt5_tools.get_symbols()
        symbol = args.symbol or mt5_cfg.get("default_symbol")
        if not symbol:
            if not symbols:
                raise RuntimeError("No symbols returned by MT5.")
            symbol = symbols[0]["name"]

        symbol_info = mt5_tools.get_symbol_info(symbol)
        rates = mt5_tools.get_rates(symbol, args.timeframe, count=args.count)
        ticks = mt5_tools.get_ticks(symbol, count=args.count)

        print("Account:")
        print(json.dumps({"login": account.get("login"), "balance": account.get("balance")}, indent=2))
        print("\nSymbol:")
        print(
            json.dumps(
                {
                    "name": symbol_info.get("name"),
                    "trade_mode": symbol_info.get("trade_mode"),
                    "digits": symbol_info.get("digits"),
                    "spread": symbol_info.get("spread"),
                },
                indent=2,
            )
        )
        print(f"\nRates fetched: {len(rates)}")
        print(f"Ticks fetched: {len(ticks)}")
    finally:
        mt5_connection.shutdown()


if __name__ == "__main__":
    main()
