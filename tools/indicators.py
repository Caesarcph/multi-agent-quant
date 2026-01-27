"""Simple technical indicator helpers."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import numpy as np


def _as_floats(values: Iterable[Any]) -> np.ndarray:
    return np.array([float(v) for v in values], dtype=float)


def calculate_sma(values: Iterable[Any], period: int) -> Optional[float]:
    if period <= 0:
        return None
    series = _as_floats(values)
    if len(series) < period:
        return None
    return float(series[-period:].mean())


def calculate_rsi(values: Iterable[Any], period: int) -> Optional[float]:
    if period <= 0:
        return None
    series = _as_floats(values)
    if len(series) <= period:
        return None
    deltas = np.diff(series)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if len(gains) < period:
        return None
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def calculate_atr(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    period: int,
) -> Optional[float]:
    if period <= 0:
        return None
    high_series = _as_floats(highs)
    low_series = _as_floats(lows)
    close_series = _as_floats(closes)
    if len(close_series) <= period:
        return None
    prev_close = close_series[:-1]
    tr = np.maximum(
        high_series[1:] - low_series[1:],
        np.maximum(
            np.abs(high_series[1:] - prev_close),
            np.abs(low_series[1:] - prev_close),
        ),
    )
    if len(tr) < period:
        return None
    return float(tr[-period:].mean())


def calculate_bollinger(
    values: Iterable[Any],
    period: int,
    stddev: float = 2.0,
) -> Optional[Dict[str, float]]:
    if period <= 0:
        return None
    series = _as_floats(values)
    if len(series) < period:
        return None
    window = series[-period:]
    mid = window.mean()
    deviation = window.std(ddof=0)
    upper = mid + stddev * deviation
    lower = mid - stddev * deviation
    return {"mid": float(mid), "upper": float(upper), "lower": float(lower)}


def calculate_bollinger_width(
    values: Iterable[Any],
    period: int,
    stddev: float = 2.0,
) -> Optional[float]:
    bands = calculate_bollinger(values, period, stddev)
    if not bands:
        return None
    mid = bands["mid"]
    if mid == 0:
        return None
    return float((bands["upper"] - bands["lower"]) / mid)


def calculate_adx(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    period: int,
) -> Optional[float]:
    if period <= 0:
        return None
    high_series = _as_floats(highs)
    low_series = _as_floats(lows)
    close_series = _as_floats(closes)
    length = len(close_series)
    if length <= period:
        return None

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []
    for i in range(1, length):
        high_diff = high_series[i] - high_series[i - 1]
        low_diff = low_series[i - 1] - low_series[i]
        plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0
        tr = max(
            high_series[i] - low_series[i],
            abs(high_series[i] - close_series[i - 1]),
            abs(low_series[i] - close_series[i - 1]),
        )
        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return None

    tr14 = sum(tr_list[:period])
    plus_dm14 = sum(plus_dm_list[:period])
    minus_dm14 = sum(minus_dm_list[:period])

    dx_values = []
    for i in range(period, len(tr_list)):
        if i > period:
            tr14 = tr14 - (tr14 / period) + tr_list[i]
            plus_dm14 = plus_dm14 - (plus_dm14 / period) + plus_dm_list[i]
            minus_dm14 = minus_dm14 - (minus_dm14 / period) + minus_dm_list[i]
        if tr14 == 0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * (plus_dm14 / tr14)
        minus_di = 100.0 * (minus_dm14 / tr14)
        denom = plus_di + minus_di
        dx = 0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return float(adx)


def calculate_indicator(
    indicator: str,
    rates: List[Dict[str, Any]],
    **kwargs: Any,
) -> Optional[Any]:
    indicator = indicator.lower()
    closes = [rate.get("close") for rate in rates if rate.get("close") is not None]
    if indicator == "sma":
        period = int(kwargs.get("period", 20))
        return calculate_sma(closes, period)
    if indicator == "rsi":
        period = int(kwargs.get("period", 14))
        return calculate_rsi(closes, period)
    if indicator == "atr":
        period = int(kwargs.get("period", 14))
        highs = [rate.get("high") for rate in rates if rate.get("high") is not None]
        lows = [rate.get("low") for rate in rates if rate.get("low") is not None]
        return calculate_atr(highs, lows, closes, period)
    if indicator == "bollinger":
        period = int(kwargs.get("period", 20))
        stddev = float(kwargs.get("stddev", 2.0))
        return calculate_bollinger(closes, period, stddev)
    if indicator == "bollinger_width":
        period = int(kwargs.get("period", 20))
        stddev = float(kwargs.get("stddev", 2.0))
        return calculate_bollinger_width(closes, period, stddev)
    if indicator == "adx":
        period = int(kwargs.get("period", 14))
        highs = [rate.get("high") for rate in rates if rate.get("high") is not None]
        lows = [rate.get("low") for rate in rates if rate.get("low") is not None]
        return calculate_adx(highs, lows, closes, period)
    return None


def compute_indicators(
    rates: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = config or {}
    closes = [rate.get("close") for rate in rates if rate.get("close") is not None]
    highs = [rate.get("high") for rate in rates if rate.get("high") is not None]
    lows = [rate.get("low") for rate in rates if rate.get("low") is not None]

    indicators: Dict[str, Any] = {}
    sma_periods = config.get("sma_periods") or []
    if sma_periods:
        sma_values: Dict[str, float] = {}
        for period in sma_periods:
            value = calculate_sma(closes, int(period))
            if value is not None:
                sma_values[str(period)] = value
        if sma_values:
            indicators["sma"] = sma_values

    rsi_period = config.get("rsi_period")
    if rsi_period is not None:
        rsi_value = calculate_rsi(closes, int(rsi_period))
        if rsi_value is not None:
            indicators["rsi"] = rsi_value

    atr_period = config.get("atr_period")
    if atr_period is not None:
        atr_value = calculate_atr(highs, lows, closes, int(atr_period))
        if atr_value is not None:
            indicators["atr"] = atr_value

    boll_cfg = config.get("bollinger") or {}
    if boll_cfg:
        boll_value = calculate_bollinger(
            closes,
            int(boll_cfg.get("period", 20)),
            float(boll_cfg.get("stddev", 2.0)),
        )
        if boll_value is not None:
            indicators["bollinger"] = boll_value

        width_value = calculate_bollinger_width(
            closes,
            int(boll_cfg.get("period", 20)),
            float(boll_cfg.get("stddev", 2.0)),
        )
        if width_value is not None:
            indicators["bollinger_width"] = width_value

    adx_period = config.get("adx_period")
    if adx_period is not None:
        adx_value = calculate_adx(highs, lows, closes, int(adx_period))
        if adx_value is not None:
            indicators["adx"] = adx_value

    return indicators
