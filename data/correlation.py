"""Correlation matrix utilities."""

from __future__ import annotations

import numpy as np


def compute_correlation_matrix(
    series_map: dict[str, list[float]],
    min_periods: int = 5,
) -> dict[str, dict[str, float]]:
    symbols = list(series_map.keys())
    matrix: dict[str, dict[str, float]] = {symbol: {} for symbol in symbols}
    if len(symbols) < 2:
        return matrix

    lengths = [len(series_map[symbol]) for symbol in symbols]
    min_len = min(lengths)
    if min_len < min_periods:
        return matrix

    data = np.array([series_map[symbol][-min_len:] for symbol in symbols], dtype=float)
    corr = np.corrcoef(data)

    for i, sym_i in enumerate(symbols):
        for j, sym_j in enumerate(symbols):
            matrix[sym_i][sym_j] = float(corr[i, j])
    return matrix
