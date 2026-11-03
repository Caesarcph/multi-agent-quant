"""Risk limits: the numbers the kernel enforces.

Built from ``risk_config.yaml`` (and ``max_spread_pips`` from
``execution_config.yaml``). Every value is checked against the hard limits in
``maq_core.config.limits`` again here, so limits constructed in code cannot be
looser than the configuration schema allows either.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta

from maq_core.config.limits import HARD_LIMITS
from maq_core.config.models import ExecutionConfig, RiskConfig

DEFAULT_PER_TRADE_RISK = 0.01
DEFAULT_DAILY_LOSS_LIMIT = 0.03


def _fraction(name: str, value: float, upper: float = 1.0) -> None:
    if not (math.isfinite(value) and 0.0 < value <= upper):
        raise ValueError(f"{name} must be in (0, {upper}], got {value!r}")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    # Account and day
    per_trade_risk: float = DEFAULT_PER_TRADE_RISK
    daily_loss_limit: float = DEFAULT_DAILY_LOSS_LIMIT
    max_drawdown: float = HARD_LIMITS.max_account_drawdown
    # (drawdown, size multiplier) pairs, sorted by drawdown
    drawdown_throttle: tuple[tuple[float, float], ...] = ()
    daily_profit_target: float | None = None
    max_daily_trades: int | None = None
    max_daily_volume: float | None = None
    # Portfolio
    max_concurrent_positions: int | None = None
    max_positions_per_symbol: int | None = None
    max_same_direction: int | None = None
    max_correlation: float | None = None
    # Pre-trade
    max_spread_pips: float | None = None
    min_reward_risk: float = 1.0
    require_take_profit: bool = False
    min_stop_atr: float = 0.5
    max_stop_atr: float = 6.0
    max_spread_to_stop: float = 0.25
    signal_ttl: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    max_price_deviation_pips: float = 10.0
    max_pending_distance_pips: float = 100.0
    news_blackout: timedelta = field(default_factory=lambda: timedelta(minutes=30))
    max_margin_usage: float = 0.5
    max_volume_per_trade: float | None = None
    require_mandate_for_demo: bool = False
    # Sizing
    min_confidence: float = 0.5
    min_confidence_multiplier: float = 0.5
    commission_per_lot: float = 0.0

    def __post_init__(self) -> None:
        _fraction("per_trade_risk", self.per_trade_risk, HARD_LIMITS.max_per_trade_risk)
        _fraction("daily_loss_limit", self.daily_loss_limit, HARD_LIMITS.max_daily_loss_limit)
        _fraction("max_drawdown", self.max_drawdown, HARD_LIMITS.max_account_drawdown)
        _fraction("max_margin_usage", self.max_margin_usage)
        _fraction("max_spread_to_stop", self.max_spread_to_stop)
        _fraction("min_confidence_multiplier", self.min_confidence_multiplier)
        if not 0.0 <= self.min_confidence < 1.0:
            raise ValueError("min_confidence must be in [0, 1)")
        if not 0.0 <= self.min_stop_atr < self.max_stop_atr:
            raise ValueError("need 0 <= min_stop_atr < max_stop_atr")
        if self.min_reward_risk < 0 or self.commission_per_lot < 0:
            raise ValueError("min_reward_risk and commission_per_lot must not be negative")
        if self.signal_ttl <= timedelta(0) or self.news_blackout < timedelta(0):
            raise ValueError("signal_ttl must be positive and news_blackout not negative")
        previous_level, previous_multiplier = 0.0, 1.0
        for level, multiplier in self.drawdown_throttle:
            if not 0.0 < level < 1.0 or not 0.0 <= multiplier <= 1.0:
                raise ValueError(f"bad drawdown_throttle entry {level}: {multiplier}")
            if level <= previous_level or multiplier > previous_multiplier:
                raise ValueError(
                    "drawdown_throttle must be sorted and its multipliers must not grow"
                )
            previous_level, previous_multiplier = level, multiplier

    @classmethod
    def from_config(cls, risk: RiskConfig, execution: ExecutionConfig | None = None) -> RiskLimits:
        """Limits from the validated config; unset values get conservative defaults."""
        trade, daily, account = risk.trade_level, risk.daily_level, risk.account_level
        pre, sizing = risk.pre_trade, risk.sizing
        return cls(
            per_trade_risk=trade.per_trade_risk or DEFAULT_PER_TRADE_RISK,
            daily_loss_limit=daily.daily_loss_limit or DEFAULT_DAILY_LOSS_LIMIT,
            max_drawdown=account.max_drawdown or HARD_LIMITS.max_account_drawdown,
            drawdown_throttle=tuple(sorted(account.drawdown_throttle.items())),
            daily_profit_target=daily.daily_profit_target,
            max_daily_trades=daily.max_daily_trades,
            max_daily_volume=daily.max_daily_volume,
            max_concurrent_positions=account.max_concurrent_positions,
            max_positions_per_symbol=account.max_positions_per_symbol,
            max_same_direction=account.max_same_direction,
            max_correlation=account.max_correlation,
            max_spread_pips=execution.max_spread_pips if execution else None,
            min_reward_risk=pre.min_reward_risk,
            require_take_profit=pre.require_take_profit,
            min_stop_atr=pre.min_stop_atr,
            max_stop_atr=pre.max_stop_atr,
            max_spread_to_stop=pre.max_spread_to_stop,
            signal_ttl=timedelta(seconds=pre.signal_ttl_seconds),
            max_price_deviation_pips=pre.max_price_deviation_pips,
            max_pending_distance_pips=pre.max_pending_distance_pips,
            news_blackout=timedelta(minutes=pre.news_blackout_minutes),
            max_margin_usage=pre.max_margin_usage,
            max_volume_per_trade=pre.max_volume_per_trade,
            require_mandate_for_demo=pre.require_mandate_for_demo,
            min_confidence=sizing.min_confidence,
            min_confidence_multiplier=sizing.min_confidence_multiplier,
            commission_per_lot=sizing.commission_per_lot,
        )

    def drawdown_multiplier(self, drawdown: float) -> float:
        """Size multiplier for the current drawdown (1.0 above every threshold)."""
        multiplier = 1.0
        for level, value in self.drawdown_throttle:
            if drawdown >= level:
                multiplier = value
        return multiplier

    def confidence_multiplier(self, confidence: float) -> float | None:
        """Size multiplier for a proposal's confidence; None below ``min_confidence``."""
        if not math.isfinite(confidence) or confidence < self.min_confidence:
            return None
        span = (min(confidence, 1.0) - self.min_confidence) / (1.0 - self.min_confidence)
        return self.min_confidence_multiplier + (1.0 - self.min_confidence_multiplier) * span
