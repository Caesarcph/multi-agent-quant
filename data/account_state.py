"""Account-level state tracking."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def load_account_state(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def update_account_state(
    path: Path,
    account_info: Dict[str, Any],
    account_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = load_account_state(path) or {}

    balance = account_info.get("balance")
    equity = account_info.get("equity")
    last_balance = float(balance) if balance is not None else None
    last_equity = float(equity) if equity is not None else None

    if state.get("initial_balance") is None and last_balance is not None:
        state["initial_balance"] = last_balance
    if state.get("initial_balance") is None and last_equity is not None:
        state["initial_balance"] = last_equity

    peak_equity = state.get("peak_equity")
    if last_equity is not None:
        if peak_equity is None or last_equity > float(peak_equity):
            state["peak_equity"] = last_equity
    elif peak_equity is None and last_balance is not None:
        state["peak_equity"] = last_balance

    initial_balance = state.get("initial_balance")
    peak_equity = state.get("peak_equity")

    drawdown = 0.0
    if peak_equity and last_equity is not None:
        drawdown = (float(peak_equity) - last_equity) / float(peak_equity)

    drawdown_from_initial = 0.0
    if initial_balance and last_equity is not None:
        drawdown_from_initial = (float(initial_balance) - last_equity) / float(initial_balance)

    state["last_balance"] = last_balance
    state["last_equity"] = last_equity
    state["drawdown"] = max(drawdown, 0.0)
    state["drawdown_from_initial"] = max(drawdown_from_initial, 0.0)
    if initial_balance is not None and last_equity is not None:
        state["pnl_since_start"] = last_equity - float(initial_balance)

    cap_cfg = (account_cfg or {}).get("capital_preservation", {})
    if cap_cfg.get("enabled", False):
        trigger = float(cap_cfg.get("trigger_drawdown", 0.5))
        resume = float(cap_cfg.get("resume_drawdown", trigger))
        in_mode = bool(state.get("capital_preservation", False))
        if in_mode:
            if state["drawdown_from_initial"] <= resume:
                in_mode = False
        else:
            if state["drawdown_from_initial"] >= trigger:
                in_mode = True
        state["capital_preservation"] = in_mode

    state["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state
