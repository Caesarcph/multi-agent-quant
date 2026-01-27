"""Tool registry for agent tool calls."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from data.correlation_builder import correlations_from_rates
from tools import mt5_tools


ToolFunc = Callable[..., Any]


def list_tools() -> List[Dict[str, Any]]:
    return [
        {"name": "get_symbols", "description": "List all available symbols."},
        {"name": "get_symbol_info", "description": "Fetch symbol contract specs (volume_min, volume_step, point, digits, margin_required)."},
        {"name": "get_symbol_tick", "description": "Fetch latest tick for a symbol (bid, ask, last)."},
        {"name": "get_rates", "description": "Fetch OHLCV rates."},
        {"name": "get_ticks", "description": "Fetch tick data."},
        {"name": "get_positions", "description": "List open positions (ticket, symbol, volume, profit, sl, tp)."},
        {"name": "get_orders", "description": "List pending orders."},
        {"name": "get_account_info", "description": "Fetch account info (balance, equity, free_margin, margin_level)."},
        {"name": "calculate_indicator", "description": "Compute indicator from rates."},
        {"name": "calculate_correlation", "description": "Compute cross-symbol correlation."},
        {"name": "calculate_margin", "description": "Calculate margin required for a trade. Args: symbol, volume, action (BUY/SELL)."},
        {"name": "calculate_lot_size", "description": "Calculate lot size from risk. Args: symbol, risk_amount, stop_loss_pips."},
    ]


def call_tool(name: str, **kwargs: Any) -> Any:
    registry = _registry()
    if name not in registry:
        raise KeyError(f"Unknown tool: {name}")
    return registry[name](**kwargs)


def _registry() -> Dict[str, ToolFunc]:
    return {
        "get_symbols": _get_symbols,
        "get_symbol_info": _get_symbol_info,
        "get_symbol_tick": _get_symbol_tick,
        "get_rates": _get_rates,
        "get_ticks": _get_ticks,
        "get_positions": _get_positions,
        "get_orders": _get_orders,
        "get_account_info": _get_account_info,
        "calculate_indicator": _calculate_indicator,
        "calculate_correlation": _calculate_correlation,
        "calculate_margin": _calculate_margin,
        "calculate_lot_size": _calculate_lot_size,
    }


def _get_symbols() -> List[Dict[str, Any]]:
    return mt5_tools.get_symbols()


def _get_symbol_info(symbol: str) -> Dict[str, Any]:
    return mt5_tools.get_symbol_info(symbol)


def _get_symbol_tick(symbol: str) -> Dict[str, Any]:
    return mt5_tools.get_symbol_tick(symbol)


def _get_rates(
    symbol: str,
    timeframe: str,
    count: int = 100,
    start_pos: int = 0,
) -> List[Dict[str, Any]]:
    return mt5_tools.get_rates(symbol, timeframe, count=count, start_pos=start_pos)


def _get_ticks(
    symbol: str,
    count: int = 100,
    from_datetime: Optional[str] = None,
) -> List[Dict[str, Any]]:
    dt = None
    if from_datetime:
        dt = datetime.fromisoformat(str(from_datetime).replace("Z", "+00:00"))
    return mt5_tools.get_ticks(symbol, count=count, from_datetime=dt)


def _get_positions() -> List[Dict[str, Any]]:
    return mt5_tools.get_positions()


def _get_orders() -> List[Dict[str, Any]]:
    return mt5_tools.get_orders()


def _get_account_info() -> Dict[str, Any]:
    return mt5_tools.get_account_info()


def _calculate_indicator(
    symbol: str,
    timeframe: str,
    indicator: str,
    count: int = 100,
    **kwargs: Any,
) -> Any:
    rates = mt5_tools.get_rates(symbol, timeframe, count=count)
    return mt5_tools.calculate_indicator(indicator, rates, **kwargs)


def _calculate_correlation(
    symbols: List[str],
    timeframe: str,
    bars: int,
) -> Dict[str, Dict[str, float]]:
    return correlations_from_rates(symbols, timeframe, bars)


def _calculate_margin(symbol: str, volume: float, action: str = "BUY") -> Dict[str, Any]:
    """Calculate margin required for a potential trade."""
    import MetaTrader5 as mt5

    order_type = mt5.ORDER_TYPE_BUY if action.upper() == "BUY" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"error": f"Cannot get tick for {symbol}"}

    price = tick.ask if action.upper() == "BUY" else tick.bid
    margin = mt5.order_calc_margin(order_type, symbol, volume, price)

    if margin is None:
        return {"error": "Failed to calculate margin"}

    account = mt5.account_info()
    return {
        "symbol": symbol,
        "volume": volume,
        "action": action,
        "price": price,
        "margin_required": margin,
        "free_margin": account.margin_free if account else 0,
        "can_afford": margin <= (account.margin_free if account else 0),
    }


def _calculate_lot_size(
    symbol: str,
    risk_amount: float,
    stop_loss_pips: float,
) -> Dict[str, Any]:
    """Calculate lot size based on risk amount and stop loss distance."""
    info = mt5_tools.get_symbol_info(symbol)
    if not info or "error" in info:
        return {"error": f"Cannot get symbol info for {symbol}"}

    point = float(info.get("point", 0.00001))
    digits = int(info.get("digits", 5))
    tick_value = float(info.get("trade_tick_value", 1.0))
    volume_min = float(info.get("volume_min", 0.01))
    volume_max = float(info.get("volume_max", 100.0))
    volume_step = float(info.get("volume_step", 0.01))

    # Pip size (for 5-digit brokers, 1 pip = 10 points)
    pip_size = point * 10 if digits in (3, 5) else point

    # Value per pip per lot
    pip_value_per_lot = tick_value * (pip_size / point)

    if pip_value_per_lot <= 0 or stop_loss_pips <= 0:
        return {"error": "Invalid pip value or stop loss"}

    # Calculate raw lot size
    raw_volume = risk_amount / (stop_loss_pips * pip_value_per_lot)

    # Clamp to symbol limits
    volume = max(volume_min, min(volume_max, raw_volume))

    # Round to step
    if volume_step > 0:
        volume = round(volume / volume_step) * volume_step
        volume = max(volume_min, volume)

    return {
        "symbol": symbol,
        "risk_amount": risk_amount,
        "stop_loss_pips": stop_loss_pips,
        "calculated_volume": round(volume, 4),
        "volume_min": volume_min,
        "volume_max": volume_max,
        "volume_step": volume_step,
        "pip_value_per_lot": pip_value_per_lot,
    }
