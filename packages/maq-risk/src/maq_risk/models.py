"""What goes into the risk kernel and what comes out."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from maq_core.config.limits import HARD_LIMITS
from maq_core.domain import (
    AccountState,
    DomainModel,
    Lots,
    OrderRequest,
    OrderType,
    Position,
    Price,
    Side,
    Symbol,
    SymbolSpec,
    Tick,
    UtcDatetime,
)
from maq_core.events import Event

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class TradeProposal(DomainModel):
    """A trade idea from the research / trading departments.

    Only structural sanity is validated here (positive, finite numbers). Whether
    the stop is on the right side, the price is real or the idea is stale is the
    kernel's job, so a bad proposal gets a recorded rejection rather than an
    exception.
    """

    symbol: Symbol
    side: Side
    order_type: OrderType = OrderType.MARKET
    # LIMIT / STOP: the entry. MARKET: the price the idea was based on (optional);
    # the kernel checks it against the market to catch invented levels.
    entry_price: Price | None = None
    stop_loss: Price | None = None
    take_profit: Price | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    created_at: UtcDatetime
    requested_volume: Lots | None = None  # advisory: can only shrink the size
    expires_at: UtcDatetime | None = None  # for LIMIT / STOP orders
    cycle_id: str | None = None
    source: str = ""
    rationale: str = ""


class Impact(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EconomicEvent(DomainModel):
    time: UtcDatetime
    currency: Currency
    impact: Impact
    title: str = ""


class KillSwitchMode(StrEnum):
    HALT_NEW = "halt_new"  # no new positions; open ones keep their stops
    FLATTEN = "flatten"  # no new positions, and close everything that is open


class KillSwitchState(DomainModel):
    mode: KillSwitchMode
    reason: str
    engaged_at: UtcDatetime
    engaged_by: str = "cli"


class Mandate(DomainModel):
    """A written permission to trade a live account, with an expiry date.

    Real-money accounts trade only under a valid mandate; the kernel also
    applies its caps (symbols, lots, risk per trade).
    """

    approved_by: Annotated[str, Field(min_length=1)]
    valid_from: UtcDatetime
    valid_until: UtcDatetime
    symbols: frozenset[str]
    max_volume_per_trade: Lots
    max_total_volume: Lots | None = None
    max_risk_per_trade: Annotated[float, Field(gt=0.0, le=HARD_LIMITS.max_per_trade_risk)] = (
        HARD_LIMITS.max_per_trade_risk
    )
    account_login: int | None = None  # None: any account
    note: str = ""

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if not self.symbols:
            raise ValueError("a mandate must allow at least one symbol")
        return self

    def problem(self, account: AccountState, symbol: str, now: datetime) -> str | None:
        if not self.valid_from <= now < self.valid_until:
            return f"mandate valid {self.valid_from:%Y-%m-%d} to {self.valid_until:%Y-%m-%d}"
        if self.account_login is not None and account.login != self.account_login:
            return f"mandate is for account {self.account_login}, not {account.login}"
        if symbol not in self.symbols:
            return f"mandate does not allow {symbol}"
        return None


class RiskContext(DomainModel):
    """Everything the kernel looks at besides the proposal. Built by the engine."""

    now: UtcDatetime
    account: AccountState
    spec: SymbolSpec
    tick: Tick
    positions: Sequence[Position] = ()
    # Reference capital for the drawdown limit and throttle (the legacy
    # "drawdown from initial balance"), and the balance when the day started.
    initial_balance: Annotated[float, Field(gt=0.0)]
    day_start_balance: Annotated[float, Field(gt=0.0)]
    trades_today: Annotated[int, Field(ge=0)] = 0
    volume_today: Annotated[float, Field(ge=0.0)] = 0.0
    atr: Annotated[float, Field(gt=0.0)] | None = None  # price units, strategy timeframe
    calendar: Sequence[EconomicEvent] = ()
    correlations: dict[str, float] = Field(default_factory=dict)  # other symbol -> rho
    market_open: bool | None = None  # None: unknown, not checked
    capital_preservation: bool = False
    kill_switch: KillSwitchState | None = None
    tripped_breakers: Sequence[str] = ()
    mandate: Mandate | None = None

    @property
    def drawdown(self) -> float:
        """Equity drawdown from the initial balance (0 when above it)."""
        return max(0.0, (self.initial_balance - self.account.equity) / self.initial_balance)

    @property
    def daily_pnl_fraction(self) -> float:
        """Today's change in equity relative to the day's starting balance."""
        return (self.account.equity - self.day_start_balance) / self.day_start_balance

    def symbol_currencies(self) -> set[str]:
        currencies = {self.spec.currency_base, self.spec.currency_profit} - {""}
        if not currencies:
            match = re.match(r"([A-Z]{3})([A-Z]{3})", self.spec.symbol.upper())
            if match:
                currencies = {match.group(1), match.group(2)}
        return currencies


class CheckOutcome(DomainModel):
    name: str
    passed: bool
    detail: str = ""


class RiskDecision(DomainModel):
    approved: bool
    symbol: Symbol
    side: Side
    order_type: OrderType
    volume: Annotated[float, Field(ge=0.0)]  # 0 when rejected
    entry_price: Price | None
    stop_loss: Price | None
    take_profit: Price | None
    expires_at: UtcDatetime | None = None
    risk_amount: Annotated[float, Field(ge=0.0)] = 0.0  # lost if the stop is hit
    risk_fraction: Annotated[float, Field(ge=0.0)] = 0.0  # of the capital used for sizing
    checks: tuple[CheckOutcome, ...]
    adjustments: tuple[str, ...] = ()
    evaluated_at: UtcDatetime

    @property
    def failed_checks(self) -> list[CheckOutcome]:
        return [check for check in self.checks if not check.passed]

    @property
    def reasons(self) -> list[str]:
        return [f"{check.name}: {check.detail}" for check in self.failed_checks]

    def to_order(
        self,
        client_order_id: str,
        *,
        magic: int = 0,
        max_slippage_points: int = 20,
    ) -> OrderRequest:
        """The order to send for an approved decision."""
        if not self.approved:
            raise ValueError("a rejected decision cannot become an order")
        return OrderRequest(
            client_order_id=client_order_id,
            symbol=self.symbol,
            side=self.side,
            volume=self.volume,
            type=self.order_type,
            price=self.entry_price,
            sl=self.stop_loss,
            tp=self.take_profit,
            max_slippage_points=max_slippage_points,
            magic=magic,
            expires_at=self.expires_at if self.order_type is not OrderType.MARKET else None,
        )


class RiskDecisionMade(Event):
    """Audit record: the proposal, what the kernel saw, and what it decided."""

    kind: Literal["risk.decision_made"] = "risk.decision_made"
    proposal: TradeProposal
    decision: RiskDecision
    equity: float
    drawdown: float
    daily_pnl_fraction: float
