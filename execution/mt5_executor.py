"""MT5 order execution helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

from execution.order_validator import validate_signal_for_execution
from execution.safety_checks import check_execution_safety, check_liquidity
from risk.position_sizer import calculate_volume
from tools import mt5_connection, mt5_tools


@dataclass
class ExecutionResult:
    status: str
    order_request: Optional[Dict[str, Any]] = None
    order_response: Optional[Dict[str, Any]] = None
    management: Optional[List[Dict[str, Any]]] = None
    errors: Optional[list[str]] = None
    notes: Optional[list[str]] = None


def _pip_size(point: float, digits: int) -> float:
    if digits in (3, 5):
        return point * 10
    return point


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


def _current_price(action: str, tick: Dict[str, Any]) -> float:
    if action == "BUY":
        return float(tick.get("ask"))
    return float(tick.get("bid"))


def _direction_from_position(position: Dict[str, Any]) -> str:
    pos_type = position.get("type")
    if pos_type is None:
        return "UNKNOWN"
    return "BUY" if int(pos_type) == 0 else "SELL"


def _reference_price(
    position: Dict[str, Any],
    action: str,
    tick: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[float]:
    reference = str(config.get("sl_tp_reference", "current")).lower()
    if reference == "entry":
        price = position.get("price_open")
        if price is not None:
            return float(price)
    price = _current_price(action, tick)
    if price <= 0:
        return None
    return float(price)


def _build_sltp_request(
    position: Dict[str, Any],
    action: str,
    symbol_info: Dict[str, Any],
    tick: Dict[str, Any],
    risk: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    sl_pips = float(risk.get("stop_loss_pips", 0.0))
    tp_pips = float(risk.get("take_profit_pips", 0.0))
    if sl_pips <= 0.0 or tp_pips <= 0.0:
        return None

    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    pip = _pip_size(point, digits)
    price = _reference_price(position, action, tick, config)
    if price is None:
        return None

    if action == "BUY":
        sl = price - sl_pips * pip
        tp = price + tp_pips * pip
    else:
        sl = price + sl_pips * pip
        tp = price - tp_pips * pip

    return {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": int(position.get("ticket")),
        "symbol": position.get("symbol"),
        "sl": sl,
        "tp": tp,
        "magic": int(config.get("magic", 0)),
        "comment": config.get("comment", "multi-agent-quant"),
    }


def _build_close_request(
    position: Dict[str, Any],
    symbol_info: Dict[str, Any],
    tick: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    direction = _direction_from_position(position)
    if direction == "UNKNOWN":
        return None
    close_action = "SELL" if direction == "BUY" else "BUY"
    order_type = mt5.ORDER_TYPE_SELL if close_action == "SELL" else mt5.ORDER_TYPE_BUY
    price = _current_price(close_action, tick)
    if price <= 0:
        return None

    volume = float(position.get("volume", 0.0))
    volume = _clamp_volume(volume, symbol_info)
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": int(position.get("ticket")),
        "symbol": position.get("symbol"),
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": int(config.get("deviation", 20)),
        "magic": int(config.get("magic", 0)),
        "comment": config.get("comment", "multi-agent-quant"),
    }


def _record_management_result(
    action: str,
    position: Dict[str, Any],
    request: Dict[str, Any],
    status: str,
    response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "action": action,
        "position": position.get("ticket"),
        "symbol": position.get("symbol"),
        "status": status,
        "request": request,
        "response": response,
        "error": error,
    }


def manage_positions(
    signal: Dict[str, Any],
    positions: List[Dict[str, Any]],
    symbol_info: Dict[str, Any],
    tick: Dict[str, Any],
    execution_cfg: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[str], bool]:
    config = execution_cfg.get("position_management", {})
    if not config.get("enabled", False):
        return [], [], False

    symbol = signal.get("symbol")
    action = signal.get("action")
    symbol_positions = [pos for pos in positions if pos.get("symbol") == symbol]
    if not symbol_positions:
        return [], [], False

    management: List[Dict[str, Any]] = []
    notes: List[str] = []
    block_entry = False
    risk = signal.get("risk", {})

    def submit_request(
        position: Dict[str, Any],
        request: Optional[Dict[str, Any]],
        action_label: str,
    ) -> None:
        if request is None:
            notes.append(f"skip_{action_label}:invalid_request")
            return
        if execution_cfg.get("dry_run", True):
            management.append(
                _record_management_result(
                    action_label, position, request, status="dry_run"
                )
            )
            return
        response = mt5.order_send(request)
        if response is None:
            code, message = mt5.last_error()
            management.append(
                _record_management_result(
                    action_label,
                    position,
                    request,
                    status="error",
                    error=f"order_send_failed:{code}:{message}",
                )
            )
            return
        management.append(
            _record_management_result(
                action_label,
                position,
                request,
                status="submitted",
                response=response._asdict(),
            )
        )

    if action == "HOLD":
        if config.get("update_stops_on_hold", False):
            for pos in symbol_positions:
                direction = _direction_from_position(pos)
                request = _build_sltp_request(pos, direction, symbol_info, tick, risk, config)
                submit_request(pos, request, "modify")
        return management, notes, True

    same_dir = [
        pos for pos in symbol_positions if _direction_from_position(pos) == action
    ]
    opp_dir = [
        pos for pos in symbol_positions if _direction_from_position(pos) != action
    ]

    if same_dir and config.get("update_stops_on_same_direction", True):
        for pos in same_dir:
            request = _build_sltp_request(pos, action, symbol_info, tick, risk, config)
            submit_request(pos, request, "modify")

    if opp_dir and config.get("close_on_opposite", True):
        for pos in opp_dir:
            request = _build_close_request(pos, symbol_info, tick, config)
            submit_request(pos, request, "close")
        if not config.get("allow_reverse", True):
            block_entry = True

    if same_dir and not config.get("allow_scale_in", False):
        block_entry = True

    return management, notes, block_entry


def build_order_request(
    signal: Dict[str, Any],
    symbol_info: Dict[str, Any],
    tick: Dict[str, Any],
    config: Dict[str, Any],
    volume: float,
) -> Dict[str, Any]:
    action = signal["action"]
    entry = signal["entry"]
    risk = signal["risk"]

    digits = int(symbol_info.get("digits", 5))
    point = float(symbol_info.get("point", 0.00001))
    pip = _pip_size(point, digits)

    price = _current_price(action, tick)
    if entry.get("type") == "limit":
        price = float(entry.get("price"))

    def _coerce_price(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    sl_price = _coerce_price(signal.get("sl_price", risk.get("stop_loss_price")))
    tp_price = _coerce_price(signal.get("tp_price", risk.get("take_profit_price")))
    use_absolute = sl_price is not None or tp_price is not None

    if action == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        if entry.get("type") == "limit":
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
        if use_absolute:
            sl = sl_price if sl_price is not None else 0.0
            tp = tp_price if tp_price is not None else 0.0
        else:
            sl_pips = float(risk.get("stop_loss_pips", 0.0))
            tp_pips = float(risk.get("take_profit_pips", 0.0))
            sl = price - sl_pips * pip
            tp = price + tp_pips * pip
    else:
        order_type = mt5.ORDER_TYPE_SELL
        if entry.get("type") == "limit":
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
        if use_absolute:
            sl = sl_price if sl_price is not None else 0.0
            tp = tp_price if tp_price is not None else 0.0
        else:
            sl_pips = float(risk.get("stop_loss_pips", 0.0))
            tp_pips = float(risk.get("take_profit_pips", 0.0))
            sl = price + sl_pips * pip
            tp = price - tp_pips * pip

    volume = _clamp_volume(volume, symbol_info)

    request = {
        "action": mt5.TRADE_ACTION_DEAL
        if entry.get("type") == "market"
        else mt5.TRADE_ACTION_PENDING,
        "symbol": signal["symbol"],
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": int(config.get("deviation", 20)),
        "magic": int(config.get("magic", 0)),
        "comment": config.get("comment", "multi-agent-quant"),
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": int(symbol_info.get("filling_mode", mt5.ORDER_FILLING_IOC)),
    }
    return request


def _success_retcodes() -> set[int]:
    return {
        int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)),
        int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
        int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
    }


def _is_filling_mode_error(response: Any) -> bool:
    if response is None:
        return False
    try:
        retcode = int(getattr(response, "retcode", 0))
    except Exception:
        retcode = None
    comment = str(getattr(response, "comment", ""))
    return retcode == 10030 or "Unsupported filling mode" in comment


def _filling_mode_candidates(symbol_info: Dict[str, Any]) -> list[int]:
    candidates: list[int] = []
    raw_mode = symbol_info.get("filling_mode")
    if raw_mode is not None:
        try:
            mode = int(raw_mode)
            if mode in (0, 1, 2):
                candidates.append(mode)
            else:
                candidates.append(mode)
        except (TypeError, ValueError):
            pass
    for mode in (
        getattr(mt5, "ORDER_FILLING_FOK", 0),
        getattr(mt5, "ORDER_FILLING_IOC", 1),
        getattr(mt5, "ORDER_FILLING_RETURN", 2),
    ):
        candidates.append(int(mode))

    seen: set[int] = set()
    ordered: list[int] = []
    for mode in candidates:
        if mode in seen:
            continue
        seen.add(mode)
        ordered.append(mode)
    return ordered


def _send_with_filling_fallback(
    request: Dict[str, Any],
    symbol_info: Dict[str, Any],
) -> tuple[Any, Dict[str, Any], list[str]]:
    notes: list[str] = []
    response = mt5.order_send(request)
    if response is not None and not _is_filling_mode_error(response):
        return response, request, notes

    candidates = _filling_mode_candidates(symbol_info)
    for mode in candidates:
        if int(request.get("type_filling", mode)) == mode:
            continue
        retry_request = dict(request)
        retry_request["type_filling"] = mode
        notes.append(f"fill_mode_retry:{mode}")
        response = mt5.order_send(retry_request)
        if response is not None and not _is_filling_mode_error(response):
            return response, retry_request, notes
    return response, request, notes


def execute_signal(
    signal: Dict[str, Any],
    execution_cfg: Dict[str, Any],
    mt5_cfg: Dict[str, Any],
    positions: Optional[List[Dict[str, Any]]] = None,
) -> ExecutionResult:
    errors = validate_signal_for_execution(signal)
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        return ExecutionResult(status="error", errors=["missing_terminal_path"])

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        symbol = signal["symbol"]
        symbol_info = mt5_tools.get_symbol_info(symbol)
        tick = mt5_tools.get_symbol_tick(symbol)
        account_info = mt5.account_info()
        if account_info is None:
            code, message = mt5.last_error()
            return ExecutionResult(status="error", errors=[f"account_info_failed:{code}:{message}"])
        account_dict = account_info._asdict()

        default_volume = float(
            execution_cfg.get("default_volume", symbol_info.get("volume_min", 0.01))
        )
        notes: list[str] = []

        if positions is None:
            positions = mt5_tools.get_positions()

        management_results: List[Dict[str, Any]] = []
        block_entry = False
        management_results, management_notes, block_entry = manage_positions(
            signal, positions, symbol_info, tick, execution_cfg
        )
        notes.extend(management_notes)
        if signal.get("action") == "HOLD" or block_entry:
            status = "managed" if management_results else "skipped"
            return ExecutionResult(
                status=status,
                management=management_results or None,
                notes=notes,
            )

        if errors:
            return ExecutionResult(
                status="invalid",
                errors=errors,
                management=management_results or None,
                notes=notes,
            )

        enforce_safety = bool(execution_cfg.get("enforce_safety", True))
        if enforce_safety:
            safety_notes = check_execution_safety(signal, symbol_info)
            liquidity_notes = check_liquidity(symbol_info, tick, execution_cfg)
            notes.extend(safety_notes)
            notes.extend(liquidity_notes)
            if safety_notes or liquidity_notes:
                errors = safety_notes + liquidity_notes
                return ExecutionResult(
                    status="blocked",
                    errors=errors,
                    management=management_results or None,
                    notes=notes,
                )

            max_spread_pips = execution_cfg.get("max_spread_pips")
            if max_spread_pips is not None:
                digits = int(symbol_info.get("digits", 5))
                point = float(symbol_info.get("point", 0.00001))
                pip = point * 10 if digits in (3, 5) else point
                spread_pips = float(symbol_info.get("spread", 0.0)) * point / pip
                if spread_pips > float(max_spread_pips):
                    reason = f"spread_exceeds_config:{spread_pips:.2f}pips"
                    notes.append(reason)
                    return ExecutionResult(
                        status="blocked",
                        errors=[reason],
                        management=management_results or None,
                        notes=notes,
                    )

        volume = default_volume
        if execution_cfg.get("use_signal_volume", True):
            volume = float(signal.get("volume", 0.0))
            if volume <= 0.0:
                return ExecutionResult(
                    status="invalid",
                    errors=["invalid_volume"],
                    management=management_results or None,
                    notes=notes,
                )
        elif execution_cfg.get("use_risk_sizing", True):
            volume, sizing_notes = calculate_volume(
                signal=signal,
                symbol_info=symbol_info,
                account_info=account_dict,
                default_volume=default_volume,
            )
            notes.extend(sizing_notes)

        request = build_order_request(signal, symbol_info, tick, execution_cfg, volume)

        if execution_cfg.get("dry_run", True):
            return ExecutionResult(
                status="dry_run",
                order_request=request,
                management=management_results or None,
                notes=notes,
            )

        response, final_request, retry_notes = _send_with_filling_fallback(
            request, symbol_info
        )
        notes.extend(retry_notes)
        if response is None:
            code, message = mt5.last_error()
            return ExecutionResult(
                status="error",
                order_request=final_request,
                errors=[f"order_send_failed:{code}:{message}"],
                management=management_results or None,
                notes=notes,
            )
        response_dict = response._asdict()
        retcode = int(response_dict.get("retcode", 0))
        comment = response_dict.get("comment", "")
        if retcode not in _success_retcodes():
            return ExecutionResult(
                status="rejected",
                order_request=final_request,
                order_response=response_dict,
                errors=[f"order_rejected:{retcode}:{comment}"],
                management=management_results or None,
                notes=notes,
            )
        return ExecutionResult(
            status="submitted",
            order_request=final_request,
            order_response=response_dict,
            management=management_results or None,
            notes=notes,
        )
    finally:
        mt5_connection.shutdown()


def render_execution_result(result: ExecutionResult) -> Dict[str, Any]:
    return {
        "status": result.status,
        "order_request": result.order_request,
        "order_response": result.order_response,
        "management": result.management,
        "errors": result.errors,
        "notes": result.notes,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
