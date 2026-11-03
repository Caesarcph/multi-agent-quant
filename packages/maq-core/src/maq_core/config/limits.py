"""Hard risk limits.

These live in code on purpose: the YAML files may only *tighten* them. A config
that asks for more risk than this fails validation, whoever edited it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardLimits:
    max_per_trade_risk: float = 0.02  # fraction of balance risked per trade
    max_daily_loss_limit: float = 0.10  # fraction of balance lost in a day before stopping
    max_account_drawdown: float = 0.50  # drawdown from initial balance that halts trading


HARD_LIMITS = HardLimits()
