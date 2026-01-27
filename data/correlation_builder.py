"""Build correlation matrix from MT5 rates."""
from __future__ import annotations

from typing import Dict, List

from data.correlation import compute_correlation_matrix
from tools import mt5_tools


def correlations_from_rates(symbols: List[str], timeframe: str, bars: int) -> Dict[str, Dict[str, float]]:
    series_map: Dict[str, List[float]] = {}
    for symbol in symbols:
        rates = mt5_tools.get_rates(symbol, timeframe, count=bars)
        closes = [float(rate["close"]) for rate in rates]
        series_map[symbol] = closes
    return compute_correlation_matrix(series_map)
