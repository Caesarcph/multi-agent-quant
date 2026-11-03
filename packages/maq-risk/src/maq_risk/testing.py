"""Builders for tests of code that uses the risk kernel.

Defaults describe a healthy demo account and a proposal every check accepts:
a 10,000 USD account at 1:100, EURUSD at 1.10000 / 1.10010, an ATR of 10 pips,
and a full-confidence BUY with a 21-point stop (measured from the ask) and a
target 2.3 times as far. Override any field by keyword.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from maq_core.domain import AccountKind, AccountState, SymbolSpec, Tick
from maq_risk.models import RiskContext, TradeProposal

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # a Tuesday
BID, ASK = 1.10000, 1.10010
EURUSD = SymbolSpec(
    symbol="EURUSD",
    digits=5,
    point=0.00001,
    tick_size=0.00001,
    tick_value=1.0,
    currency_base="EUR",
    currency_profit="USD",
    currency_margin="EUR",
)


def make_account(**overrides: Any) -> AccountState:
    data: dict[str, Any] = {
        "kind": AccountKind.DEMO,
        "currency": "USD",
        "balance": 10_000.0,
        "equity": 10_000.0,
        "margin": 0.0,
        "leverage": 100,
        "login": 1234,
    }
    data.update(overrides)
    data.setdefault("margin_free", data["equity"] - data["margin"])
    return AccountState.model_validate(data)


def make_context(**overrides: Any) -> RiskContext:
    data: dict[str, Any] = {
        "now": T0,
        "account": make_account(),
        "spec": EURUSD,
        "tick": Tick(symbol="EURUSD", time=T0, bid=BID, ask=ASK),
        "initial_balance": 10_000.0,
        "day_start_balance": 10_000.0,
        "atr": 0.0010,
    }
    data.update(overrides)
    return RiskContext.model_validate(data)


def make_proposal(**overrides: Any) -> TradeProposal:
    data: dict[str, Any] = {
        "symbol": "EURUSD",
        "side": "BUY",
        "stop_loss": 1.09800,
        "take_profit": 1.10500,
        "confidence": 1.0,
        "created_at": T0,
    }
    data.update(overrides)
    return TradeProposal.model_validate(data)
