"""Domain types shared by brokers, the risk kernel, the engine and the agents.

Conventions used everywhere in the platform:

* prices are floats in the symbol's quote currency, volumes are lots, and money
  (balance, profit, commission, margin) is in the account currency;
* every timestamp is timezone-aware UTC -- naive datetimes are rejected;
* models are immutable and reject NaN / infinity, so a bad number fails loudly
  where it is created instead of slipping past a comparison later on.

Floats rather than Decimal: the MetaTrader 5 API speaks floats, and the helpers
here (`floor_to_step`, `SymbolSpec.normalize_price`) take care of the rounding
that matters -- volumes are floored onto the broker's grid, never rounded up.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# Tolerance for float noise when comparing against a volume or price grid,
# e.g. 0.3 / 0.1 == 2.9999999999999996.
GRID_EPSILON = 1e-9


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware (UTC)")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]
Price = Annotated[float, Field(gt=0.0)]
Lots = Annotated[float, Field(gt=0.0)]
Money = float
Symbol = Annotated[str, Field(min_length=1, max_length=64)]

# Fits into an MT5 order comment (31 characters), which is where the MT5 adapter
# stores it so that a resent order can be recognised.
CLIENT_ORDER_ID_MAX_LENGTH = 31
ClientOrderId = Annotated[
    str,
    Field(min_length=1, max_length=CLIENT_ORDER_ID_MAX_LENGTH, pattern=r"^[A-Za-z0-9._:-]+$"),
]


class DomainModel(BaseModel):
    """Immutable, strict base for domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def floor_to_step(value: float, step: float) -> float:
    """Round ``value`` down to a multiple of ``step``.

    Exact multiples stay where they are despite float error, and the result is
    rounded to ten decimals so ``0.07`` comes back as ``0.07``.
    """
    if step <= 0 or not math.isfinite(step):
        raise ValueError(f"step must be a positive number, got {step!r}")
    return round(math.floor(value / step + GRID_EPSILON) * step, 10)


def make_client_order_id(*parts: object, prefix: str = "maq") -> str:
    """A deterministic idempotency key, e.g. from ``(cycle_id, symbol, side)``.

    The same parts always give the same key, so retrying a decision can never
    produce a second order. The key is short enough for an MT5 order comment.
    """
    if not parts:
        raise ValueError("make_client_order_id needs at least one part")
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    key = f"{prefix}-{digest[:20]}"
    if len(key) > CLIENT_ORDER_ID_MAX_LENGTH:
        raise ValueError(f"prefix {prefix!r} is too long for a client order id")
    return key


# =============================================================================
# Enumerations
# =============================================================================
class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL: profit = sign * (exit - entry) * value."""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"  # BUY below / SELL above the market
    STOP = "STOP"  # BUY above / SELL below the market


class OrderStatus(StrEnum):
    NEW = "NEW"  # recorded locally, not sent yet
    SUBMITTED = "SUBMITTED"  # sent; the broker's answer has not arrived (or was lost)
    ACCEPTED = "ACCEPTED"  # pending order resting at the broker
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
)


class RejectReason(StrEnum):
    """Why a broker refused an order, normalised across brokers.

    The broker's own code (for MT5, the retcode) is kept in
    ``OrderResult.broker_code`` for diagnosis.
    """

    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    INVALID_VOLUME = "INVALID_VOLUME"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_STOPS = "INVALID_STOPS"
    NO_MONEY = "NO_MONEY"
    MARKET_CLOSED = "MARKET_CLOSED"
    TRADE_DISABLED = "TRADE_DISABLED"
    PRICE_CHANGED = "PRICE_CHANGED"  # requote / price moved beyond the allowed slippage
    NOT_FOUND = "NOT_FOUND"  # cancel / close of something the broker does not know
    OTHER = "OTHER"


class CloseReason(StrEnum):
    CLIENT = "CLIENT"  # closed by us
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_OUT = "STOP_OUT"  # margin call liquidation
    OTHER = "OTHER"  # manual close in the terminal, broker action, ...


class AccountKind(StrEnum):
    PAPER = "paper"  # simulated locally, no broker involved
    DEMO = "demo"
    CONTEST = "contest"
    REAL = "real"

    @property
    def is_real_money(self) -> bool:
        return self is AccountKind.REAL


class Timeframe(StrEnum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def duration(self) -> timedelta:
        """Bar length. MN1 is treated as 30 days, which is fine for staleness checks."""
        return _TIMEFRAME_DURATIONS[self]


_TIMEFRAME_DURATIONS = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
    Timeframe.W1: timedelta(weeks=1),
    Timeframe.MN1: timedelta(days=30),
}


# =============================================================================
# Market data
# =============================================================================
class SymbolSpec(DomainModel):
    """Contract specification of a tradable symbol (MT5 ``symbol_info``).

    ``tick_value`` is what one ``tick_size`` move is worth for one lot, in the
    account currency. MT5 recomputes it at current rates for cross pairs, so a
    spec is a snapshot: refresh it before sizing an order.
    """

    symbol: Symbol
    digits: int = Field(ge=0, le=10)
    point: Price
    tick_size: Price
    tick_value: Price
    contract_size: Price = 100_000.0
    volume_min: Lots = 0.01
    volume_max: Lots = 100.0
    volume_step: Lots = 0.01
    stops_level: int = Field(default=0, ge=0)  # min SL/TP distance from the price, in points
    freeze_level: int = Field(default=0, ge=0)
    currency_base: str = ""
    currency_profit: str = ""
    currency_margin: str = ""
    margin_rate: Annotated[float, Field(gt=0.0)] = 1.0
    trade_allowed: bool = True

    @model_validator(mode="after")
    def _volume_range(self) -> Self:
        if self.volume_min > self.volume_max:
            raise ValueError("volume_min must not exceed volume_max")
        return self

    # ------------------------------------------------------------------ pips
    @property
    def pip_size(self) -> float:
        """One pip: 10 points on 3/5-digit quotes (FX convention), else one point."""
        return self.point * 10 if self.digits in (3, 5) else self.point

    def pips_to_price(self, pips: float) -> float:
        return pips * self.pip_size

    def price_to_pips(self, distance: float) -> float:
        return abs(distance) / self.pip_size

    # ------------------------------------------------------------------ money
    def value_per_price_unit(self, volume: float = 1.0) -> float:
        """Account-currency P&L of a 1.0 price move for ``volume`` lots."""
        return self.tick_value / self.tick_size * volume

    def pip_value(self, volume: float = 1.0) -> float:
        """Account-currency value of one pip for ``volume`` lots."""
        return self.value_per_price_unit(volume) * self.pip_size

    def profit(self, side: Side, volume: float, open_price: float, close_price: float) -> float:
        return side.sign * (close_price - open_price) * self.value_per_price_unit(volume)

    def notional(self, volume: float, price: float) -> float:
        """Position value in the account currency.

        ``price * tick_value / tick_size`` converts through whatever currency the
        quote is in: for EURUSD on a USD account it is ``lots * 100_000 * price``,
        for USDJPY it comes out as ``lots * 100_000`` USD.
        """
        return volume * price * self.value_per_price_unit()

    def margin(self, volume: float, price: float, leverage: int) -> float:
        """Approximate initial margin (FX formula). Brokers compute their own."""
        return self.notional(volume, price) * self.margin_rate / max(leverage, 1)

    # ------------------------------------------------------------------ grids
    def floor_volume(self, volume: float) -> float:
        """Largest tradable volume not above ``volume``; 0.0 if below ``volume_min``.

        Never rounds up: a risk-sized volume must not grow past its budget, and a
        volume under the broker minimum is a reason to skip the trade, not to
        trade the minimum.
        """
        if not math.isfinite(volume) or volume <= 0:
            return 0.0
        lots = floor_to_step(min(volume, self.volume_max), self.volume_step)
        return lots if lots >= self.volume_min - GRID_EPSILON else 0.0

    def is_valid_volume(self, volume: float) -> bool:
        if not math.isfinite(volume):
            return False
        if volume < self.volume_min - GRID_EPSILON or volume > self.volume_max + GRID_EPSILON:
            return False
        steps = volume / self.volume_step
        return abs(steps - round(steps)) < 1e-6

    def normalize_price(self, price: float) -> float:
        """Snap a price onto the tick grid and the symbol's digits."""
        return round(round(price / self.tick_size) * self.tick_size, self.digits)

    @property
    def min_stop_distance(self) -> float:
        """Minimum SL/TP distance from the current price (``stops_level`` points)."""
        return self.stops_level * self.point


class Tick(DomainModel):
    symbol: Symbol
    time: UtcDatetime
    bid: Price
    ask: Price

    @model_validator(mode="after")
    def _not_crossed(self) -> Self:
        if self.ask < self.bid:
            raise ValueError(f"crossed quote: ask {self.ask} < bid {self.bid}")
        return self

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def entry_price(self, side: Side) -> float:
        """Price a market order on ``side`` fills at: buy at the ask, sell at the bid."""
        return self.ask if side is Side.BUY else self.bid

    def exit_price(self, side: Side) -> float:
        """Price a position on ``side`` closes at: longs sell at the bid, shorts buy at the ask."""
        return self.bid if side is Side.BUY else self.ask


class Bar(DomainModel):
    """One OHLC bar; ``time`` is the bar's open time."""

    time: UtcDatetime
    open: Price
    high: Price
    low: Price
    close: Price
    tick_volume: Annotated[float, Field(ge=0.0)] = 0.0
    spread: Annotated[int, Field(ge=0)] = 0  # points
    real_volume: Annotated[float, Field(ge=0.0)] = 0.0

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close
        ):
            raise ValueError(
                f"inconsistent bar at {self.time.isoformat()}: "
                f"O={self.open} H={self.high} L={self.low} C={self.close}"
            )
        return self


# =============================================================================
# Account, positions, orders
# =============================================================================
class AccountState(DomainModel):
    kind: AccountKind
    currency: Annotated[str, Field(min_length=1)]
    balance: Money
    equity: Money
    margin: Annotated[float, Field(ge=0.0)] = 0.0
    margin_free: Money
    leverage: Annotated[int, Field(ge=1)] = 100
    login: int | None = None
    server: str | None = None
    trade_allowed: bool = True

    @property
    def margin_level(self) -> float | None:
        """Equity / margin in percent; None without open positions."""
        return self.equity / self.margin * 100 if self.margin > 0 else None


class Position(DomainModel):
    position_id: Annotated[str, Field(min_length=1)]
    symbol: Symbol
    side: Side
    volume: Lots
    open_price: Price
    open_time: UtcDatetime
    sl: Price | None = None
    tp: Price | None = None
    current_price: Price | None = None
    profit: Money = 0.0  # floating P&L, excluding swap and commission
    swap: Money = 0.0
    commission: Money = 0.0
    magic: Annotated[int, Field(ge=0)] = 0
    client_order_id: str | None = None  # the order that opened it, when known

    @property
    def net_profit(self) -> float:
        return self.profit + self.swap + self.commission


def _check_stops(side: Side, reference: float | None, sl: float | None, tp: float | None) -> None:
    """SL below and TP above the price for a BUY, the other way round for a SELL."""
    if sl is not None and tp is not None and side.sign * (tp - sl) <= 0:
        raise ValueError(f"{side} order needs {'sl < tp' if side is Side.BUY else 'sl > tp'}")
    if reference is None:
        return
    if sl is not None and side.sign * (reference - sl) <= 0:
        raise ValueError(f"stop loss {sl} is on the wrong side of {reference} for a {side}")
    if tp is not None and side.sign * (tp - reference) <= 0:
        raise ValueError(f"take profit {tp} is on the wrong side of {reference} for a {side}")


class OrderRequest(DomainModel):
    """An order as the platform asks for it.

    ``client_order_id`` is the idempotency key: a broker that has already seen it
    returns the earlier result instead of trading again.
    """

    client_order_id: ClientOrderId
    symbol: Symbol
    side: Side
    volume: Lots
    type: OrderType = OrderType.MARKET
    # Entry price for LIMIT / STOP. For MARKET it is optional: the price the
    # decision was based on, used to check slippage before sending.
    price: Price | None = None
    sl: Price | None = None
    tp: Price | None = None
    max_slippage_points: Annotated[int, Field(ge=0)] = 20
    magic: Annotated[int, Field(ge=0)] = 0
    expires_at: UtcDatetime | None = None  # LIMIT / STOP only

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.type is not OrderType.MARKET and self.price is None:
            raise ValueError(f"a {self.type} order needs a price")
        if self.type is OrderType.MARKET and self.expires_at is not None:
            raise ValueError("expires_at only applies to LIMIT and STOP orders")
        _check_stops(self.side, self.price, self.sl, self.tp)
        return self


class WorkingOrder(DomainModel):
    """A LIMIT / STOP order resting at the broker."""

    client_order_id: ClientOrderId
    broker_order_id: str | None = None
    symbol: Symbol
    side: Side
    type: OrderType
    volume: Lots
    price: Price
    sl: Price | None = None
    tp: Price | None = None
    placed_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    magic: Annotated[int, Field(ge=0)] = 0


class OrderResult(DomainModel):
    """The broker's answer to an order (or to a close / cancel request)."""

    client_order_id: ClientOrderId
    status: OrderStatus
    symbol: Symbol
    side: Side
    requested_volume: Lots
    filled_volume: Annotated[float, Field(ge=0.0)] = 0.0
    avg_fill_price: Price | None = None
    time: UtcDatetime
    broker_order_id: str | None = None
    position_id: str | None = None
    commission: Money = 0.0
    reject_reason: RejectReason | None = None
    broker_code: str | None = None  # e.g. the MT5 retcode
    message: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status is OrderStatus.REJECTED and self.reject_reason is None:
            raise ValueError("a rejected order needs a reject_reason")
        if self.status is not OrderStatus.REJECTED and self.reject_reason is not None:
            raise ValueError("reject_reason is only for rejected orders")
        if self.filled_volume > self.requested_volume + GRID_EPSILON:
            raise ValueError("filled_volume exceeds requested_volume")
        if self.filled_volume > 0 and self.avg_fill_price is None:
            raise ValueError("a fill needs avg_fill_price")
        return self

    @property
    def is_filled(self) -> bool:
        return self.status is OrderStatus.FILLED

    @property
    def is_rejected(self) -> bool:
        return self.status is OrderStatus.REJECTED

    @classmethod
    def rejected(
        cls,
        order: OrderRequest,
        reason: RejectReason,
        message: str,
        *,
        time: datetime,
        broker_code: str | None = None,
    ) -> OrderResult:
        return cls(
            client_order_id=order.client_order_id,
            status=OrderStatus.REJECTED,
            symbol=order.symbol,
            side=order.side,
            requested_volume=order.volume,
            time=time,
            reject_reason=reason,
            broker_code=broker_code,
            message=message,
        )


class ClosedTrade(DomainModel):
    """A position (or part of one) that has been closed."""

    position_id: Annotated[str, Field(min_length=1)]
    symbol: Symbol
    side: Side
    volume: Lots
    open_price: Price
    close_price: Price
    open_time: UtcDatetime
    close_time: UtcDatetime
    profit: Money
    swap: Money = 0.0
    commission: Money = 0.0
    reason: CloseReason
    client_order_id: str | None = None  # the order that opened the position

    @property
    def net_profit(self) -> float:
        return self.profit + self.swap + self.commission
