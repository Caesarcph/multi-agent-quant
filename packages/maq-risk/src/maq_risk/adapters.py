"""Bridges from the legacy signal format to ``TradeProposal``."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from maq_core.domain import OrderType, Side, SymbolSpec, Tick
from maq_risk.models import TradeProposal


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def proposal_from_signal(
    signal: Mapping[str, Any],
    *,
    tick: Tick,
    spec: SymbolSpec,
    created_at: datetime,
    source: str = "",
    cycle_id: str | None = None,
) -> TradeProposal | None:
    """Turn a signal from ``agents/prompts/output_schema.json`` into a proposal.

    Returns None for HOLD. ``created_at`` is the time of the market snapshot the
    signal was based on -- not the model's own ``timestamp`` field, which the
    model writes and which therefore cannot be trusted for the age check.
    Stops given in pips are measured from the entry price (the current market
    for market orders), the same way the legacy executor does it; absolute
    ``sl_price`` / ``tp_price`` win when present.
    """
    action = str(signal.get("action", "")).upper()
    if action not in ("BUY", "SELL"):
        return None
    side = Side(action)
    entry = signal.get("entry") if isinstance(signal.get("entry"), Mapping) else {}
    risk = signal.get("risk") if isinstance(signal.get("risk"), Mapping) else {}
    assert isinstance(entry, Mapping) and isinstance(risk, Mapping)

    order_type = (
        OrderType.LIMIT if str(entry.get("type", "")).lower() == "limit" else OrderType.MARKET
    )
    entry_price = _positive(entry.get("price"))
    reference = entry_price if order_type is OrderType.LIMIT and entry_price else None
    reference = reference or tick.entry_price(side)

    def level(absolute: Any, pips: Any, direction: int) -> float | None:
        price = _positive(absolute)
        if price is None and (distance := _positive(pips)) is not None:
            price = reference + direction * side.sign * spec.pips_to_price(distance)
        return spec.normalize_price(price) if price is not None and price > 0 else None

    stop_loss = level(
        signal.get("sl_price", risk.get("stop_loss_price")), risk.get("stop_loss_pips"), -1
    )
    take_profit = level(
        signal.get("tp_price", risk.get("take_profit_price")), risk.get("take_profit_pips"), +1
    )
    ttl = _positive(signal.get("ttl_minutes"))
    confidence = float(signal.get("confidence", 0.0) or 0.0)
    return TradeProposal(
        symbol=str(signal.get("symbol") or spec.symbol),
        side=side,
        order_type=order_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=min(max(confidence, 0.0), 1.0) if math.isfinite(confidence) else 0.0,
        created_at=created_at,
        requested_volume=_positive(signal.get("volume")),
        expires_at=(
            created_at + timedelta(minutes=ttl) if ttl and order_type is OrderType.LIMIT else None
        ),
        cycle_id=cycle_id,
        source=source or str(signal.get("model", "")),
        rationale=str((signal.get("analysis") or {}).get("setup", ""))
        if isinstance(signal.get("analysis"), Mapping)
        else "",
    )
