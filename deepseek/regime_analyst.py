"""DeepSeek regime analysis for ambiguous market regimes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from deepseek.client import DeepSeekClient
from deepseek.utils import parse_json_output
from tools import indicators, mt5_connection, mt5_tools


def _trim_rates(rates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trimmed: List[Dict[str, Any]] = []
    for rate in rates:
        entry = {
            "time": rate.get("time"),
            "open": rate.get("open"),
            "high": rate.get("high"),
            "low": rate.get("low"),
            "close": rate.get("close"),
            "tick_volume": rate.get("tick_volume"),
        }
        if entry.get("time") is not None:
            ts = datetime.fromtimestamp(float(entry["time"]), tz=timezone.utc)
            entry["time"] = ts.isoformat().replace("+00:00", "Z")
        trimmed.append(entry)
    return trimmed


def build_multi_tf_data(
    symbol: str,
    mt5_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    timeframes = config.get("timeframes", {"H1": 100, "H4": 50, "D1": 20})
    indicator_cfg = config.get("indicator_periods", {})
    multi_tf: Dict[str, Any] = {"symbol": symbol, "timeframes": {}, "indicators": {}}

    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required for regime analysis")

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        for tf, bars in timeframes.items():
            rates = mt5_tools.get_rates(symbol, tf, count=int(bars))
            multi_tf["timeframes"][tf] = _trim_rates(rates)
    finally:
        mt5_connection.shutdown()

    h1_rates = multi_tf["timeframes"].get("H1") or []
    closes = [rate.get("close") for rate in h1_rates if rate.get("close") is not None]
    highs = [rate.get("high") for rate in h1_rates if rate.get("high") is not None]
    lows = [rate.get("low") for rate in h1_rates if rate.get("low") is not None]

    adx_period = int(indicator_cfg.get("adx", 14))
    atr_period = int(indicator_cfg.get("atr", 14))
    bb_period = int(indicator_cfg.get("bollinger_period", 20))
    bb_stddev = float(indicator_cfg.get("bollinger_stddev", 2.0))

    multi_tf["indicators"]["adx"] = indicators.calculate_adx(
        highs, lows, closes, adx_period
    )
    multi_tf["indicators"]["atr"] = indicators.calculate_atr(
        highs, lows, closes, atr_period
    )
    multi_tf["indicators"]["bollinger_width"] = indicators.calculate_bollinger_width(
        closes, bb_period, bb_stddev
    )

    return multi_tf


def build_regime_prompt(symbol: str, multi_tf_data: Dict[str, Any]) -> str:
    payload = {"symbol": symbol, "multi_timeframe_data": multi_tf_data}
    return (
        "You are a market regime specialist. Provide a deeper regime analysis.\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "primary_regime": "TRENDING_UP|TRENDING_DOWN|RANGING|VOLATILE",\n'
        '  "regime_strength": "STRONG|MODERATE|WEAK",\n'
        '  "regime_age": "EARLY|MATURE|LATE",\n'
        '  "expected_duration": "SHORT|MEDIUM|LONG",\n'
        '  "reasoning": "brief explanation"\n'
        "}\n\n"
        f"Data:\n{json.dumps(payload, indent=2)}\n"
    )


class DeepSeekRegimeAnalyst:
    def __init__(self, client: DeepSeekClient) -> None:
        self._client = client
        self.last_raw_output: Optional[str] = None

    def analyze_regime(self, symbol: str, multi_tf_data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = build_regime_prompt(symbol, multi_tf_data)
        raw_output = self._client.analyze(prompt, task="regime_analysis")
        self.last_raw_output = raw_output
        parsed, error = parse_json_output(raw_output)
        return {
            "prompt": prompt,
            "raw_output": raw_output,
            "parsed": parsed,
            "error": error,
        }
