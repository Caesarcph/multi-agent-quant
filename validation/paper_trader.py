"""Paper trading simulator using live MT5 ticks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools import mt5_connection, mt5_tools


@dataclass
class PaperTradeResult:
    status: str
    state: Dict[str, Any]
    closed_trades: List[Dict[str, Any]]
    notes: List[str]


def _pip_size(symbol_info: Dict[str, Any]) -> float:
    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    return point * 10 if digits in (3, 5) else point


def _pip_value(symbol_info: Dict[str, Any]) -> float:
    tick_size = float(symbol_info.get("trade_tick_size", 0.0))
    tick_value = float(symbol_info.get("trade_tick_value", 0.0))
    pip_size = _pip_size(symbol_info)
    if tick_size <= 0.0 or tick_value <= 0.0:
        return 1.0
    return tick_value * (pip_size / tick_size)


def _current_price(action: str, tick: Dict[str, Any]) -> float:
    if action == "BUY":
        return float(tick.get("ask", 0.0))
    return float(tick.get("bid", 0.0))


def _clamp_volume(volume: float, symbol_info: Dict[str, Any]) -> float:
    min_vol = float(symbol_info.get("volume_min", 0.0))
    max_vol = float(symbol_info.get("volume_max", 0.0))
    step = float(symbol_info.get("volume_step", 0.0))

    volume = max(min_vol, volume)
    if max_vol > 0:
        volume = min(max_vol, volume)
    if step > 0:
        volume = round(volume / step) * step
        volume = max(min_vol, volume)
    return float(f"{volume:.4f}")


def _position_direction(position: Dict[str, Any]) -> str:
    return str(position.get("direction", "")).upper()


def _calc_profit(
    entry_price: float,
    exit_price: float,
    volume: float,
    direction: str,
    symbol_info: Dict[str, Any],
) -> float:
    pip_size = _pip_size(symbol_info)
    pip_value = _pip_value(symbol_info)
    if direction == "BUY":
        diff = exit_price - entry_price
    else:
        diff = entry_price - exit_price
    return (diff / pip_size) * pip_value * volume


def _close_position(
    position: Dict[str, Any],
    exit_price: float,
    symbol_info: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    profit = _calc_profit(
        entry_price=float(position["entry_price"]),
        exit_price=exit_price,
        volume=float(position["volume"]),
        direction=_position_direction(position),
        symbol_info=symbol_info,
    )
    return {
        "id": position.get("id"),
        "symbol": position.get("symbol"),
        "direction": _position_direction(position),
        "volume": position.get("volume"),
        "entry_price": position.get("entry_price"),
        "exit_price": exit_price,
        "profit": profit,
        "entry_time": position.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": reason,
    }


class PaperTrader:
    def __init__(self, state_path: Path, config: Dict[str, Any]) -> None:
        self._state_path = state_path
        self._config = config

    def apply_signal(self, signal: Dict[str, Any], mt5_cfg: Dict[str, Any]) -> PaperTradeResult:
        terminal_path = mt5_cfg.get("terminal_path")
        if not terminal_path:
            return PaperTradeResult(status="error", state={}, closed_trades=[], notes=["missing_terminal_path"])

        mt5_connection.initialize(
            terminal_path=terminal_path,
            timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
        )
        try:
            symbol = signal.get("symbol")
            if not symbol:
                return PaperTradeResult(status="invalid", state={}, closed_trades=[], notes=["missing_symbol"])

            symbol_info = mt5_tools.get_symbol_info(symbol)
            tick = mt5_tools.get_symbol_tick(symbol)
            account_info = mt5_tools.get_account_info()
            state = self._load_state(account_info)

            notes: List[str] = []
            closed_trades: List[Dict[str, Any]] = []

            info_map, tick_map = self._load_symbol_context(state, symbol_info, tick)
            state, closed_trades = self._update_positions(state, info_map, tick_map)
            if closed_trades:
                state.setdefault("trades", []).extend(closed_trades)

            action = signal.get("action")
            if action in {"BUY", "SELL"}:
                state, notes, new_trades = self._handle_entry(
                    state, signal, symbol_info, tick
                )
                closed_trades.extend(new_trades)
                if new_trades:
                    state.setdefault("trades", []).extend(new_trades)

            state["equity"] = state["balance"] + self._unrealized_pnl(
                state, info_map, tick_map
            )
            state["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save_state(state)

            return PaperTradeResult(
                status="ok",
                state=state,
                closed_trades=closed_trades,
                notes=notes,
            )
        finally:
            mt5_connection.shutdown()

    def _handle_entry(
        self,
        state: Dict[str, Any],
        signal: Dict[str, Any],
        symbol_info: Dict[str, Any],
        tick: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
        notes: List[str] = []
        closed_trades: List[Dict[str, Any]] = []
        symbol = signal.get("symbol")
        direction = signal.get("action")
        volume = float(signal.get("volume", 0.0))
        if volume <= 0.0:
            notes.append("invalid_volume")
            return state, notes, closed_trades

        allow_scale_in = bool(self._config.get("allow_scale_in", False))
        allow_reverse = bool(self._config.get("allow_reverse", True))
        volume = _clamp_volume(volume, symbol_info)

        positions = state.get("open_positions", [])
        same_dir = [pos for pos in positions if pos.get("symbol") == symbol and _position_direction(pos) == direction]
        opp_dir = [pos for pos in positions if pos.get("symbol") == symbol and _position_direction(pos) != direction]

        for pos in opp_dir:
            exit_price = _current_price("BUY" if direction == "SELL" else "SELL", tick)
            closed = _close_position(pos, exit_price, symbol_info, reason="reverse")
            closed_trades.append(closed)
            state["balance"] += closed["profit"]
            positions.remove(pos)

        if opp_dir and not allow_reverse:
            notes.append("reverse_blocked")
            return state, notes, closed_trades

        if same_dir and not allow_scale_in:
            notes.append("scale_in_blocked")
            return state, notes, closed_trades

        entry = signal.get("entry", {}) or {}
        price = _current_price(direction, tick)
        if entry.get("type") == "limit" and entry.get("price") is not None:
            price = float(entry.get("price"))

        risk = signal.get("risk", {}) or {}
        sl_pips = float(risk.get("stop_loss_pips", 0.0))
        tp_pips = float(risk.get("take_profit_pips", 0.0))
        pip = _pip_size(symbol_info)
        stop_loss = None
        take_profit = None
        if sl_pips > 0:
            stop_loss = price - sl_pips * pip if direction == "BUY" else price + sl_pips * pip
        if tp_pips > 0:
            take_profit = price + tp_pips * pip if direction == "BUY" else price - tp_pips * pip

        position_id = int(state.get("next_id", 1))
        state["next_id"] = position_id + 1
        positions.append(
            {
                "id": position_id,
                "symbol": symbol,
                "direction": direction,
                "volume": volume,
                "entry_price": price,
                "entry_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
        )
        state["open_positions"] = positions
        return state, notes, closed_trades

    def _load_symbol_context(
        self,
        state: Dict[str, Any],
        symbol_info: Dict[str, Any],
        tick: Dict[str, Any],
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        info_map: Dict[str, Dict[str, Any]] = {}
        tick_map: Dict[str, Dict[str, Any]] = {}
        symbols = {pos.get("symbol") for pos in state.get("open_positions", [])}
        if symbol_info.get("name"):
            symbols.add(symbol_info.get("name"))

        for sym in symbols:
            if not sym:
                continue
            if sym == symbol_info.get("name"):
                info_map[sym] = symbol_info
                tick_map[sym] = tick
                continue
            info_map[sym] = mt5_tools.get_symbol_info(sym)
            tick_map[sym] = mt5_tools.get_symbol_tick(sym)
        return info_map, tick_map

    def _update_positions(
        self,
        state: Dict[str, Any],
        info_map: Dict[str, Dict[str, Any]],
        tick_map: Dict[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        positions = state.get("open_positions", [])
        closed_trades: List[Dict[str, Any]] = []

        for pos in list(positions):
            symbol = pos.get("symbol")
            symbol_info = info_map.get(symbol)
            tick = tick_map.get(symbol)
            if not symbol_info or not tick:
                continue
            direction = _position_direction(pos)
            price_buy = _current_price("BUY", tick)
            price_sell = _current_price("SELL", tick)
            price = price_sell if direction == "BUY" else price_buy
            stop_loss = pos.get("stop_loss")
            take_profit = pos.get("take_profit")
            closed = None
            if stop_loss is not None:
                if (direction == "BUY" and price <= stop_loss) or (
                    direction == "SELL" and price >= stop_loss
                ):
                    closed = _close_position(pos, stop_loss, symbol_info, reason="stop_loss")
            if closed is None and take_profit is not None:
                if (direction == "BUY" and price >= take_profit) or (
                    direction == "SELL" and price <= take_profit
                ):
                    closed = _close_position(pos, take_profit, symbol_info, reason="take_profit")

            if closed:
                closed_trades.append(closed)
                state["balance"] += closed["profit"]
                positions.remove(pos)

        state["open_positions"] = positions
        return state, closed_trades

    def _unrealized_pnl(
        self,
        state: Dict[str, Any],
        info_map: Dict[str, Dict[str, Any]],
        tick_map: Dict[str, Dict[str, Any]],
    ) -> float:
        total = 0.0
        for pos in state.get("open_positions", []):
            symbol = pos.get("symbol")
            symbol_info = info_map.get(symbol)
            tick = tick_map.get(symbol)
            if not symbol_info or not tick:
                continue
            direction = _position_direction(pos)
            price = _current_price("SELL", tick) if direction == "BUY" else _current_price("BUY", tick)
            total += _calc_profit(
                entry_price=float(pos.get("entry_price")),
                exit_price=price,
                volume=float(pos.get("volume")),
                direction=direction,
                symbol_info=symbol_info,
            )
        return total

    def _load_state(self, account_info: Dict[str, Any]) -> Dict[str, Any]:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        initial_balance = self._config.get("initial_balance")
        if initial_balance is None:
            initial_balance = account_info.get("balance")
        if initial_balance is None:
            initial_balance = 0.0

        return {
            "initial_balance": float(initial_balance),
            "balance": float(initial_balance),
            "equity": float(initial_balance),
            "open_positions": [],
            "trades": [],
            "next_id": 1,
        }

    def _save_state(self, state: Dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
