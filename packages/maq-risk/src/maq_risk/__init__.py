"""Deterministic risk kernel for multi-agent-quant."""

from maq_risk.adapters import proposal_from_signal
from maq_risk.breakers import BudgetBreaker, CircuitBreakers, RateBreaker
from maq_risk.kernel import RiskInvariantError, RiskKernel
from maq_risk.kill_switch import KillSwitch
from maq_risk.limits import RiskLimits
from maq_risk.models import (
    CheckOutcome,
    EconomicEvent,
    Impact,
    KillSwitchMode,
    KillSwitchState,
    Mandate,
    RiskContext,
    RiskDecision,
    RiskDecisionMade,
    TradeProposal,
)

__version__ = "0.1.0"

__all__ = [
    "BudgetBreaker",
    "CheckOutcome",
    "CircuitBreakers",
    "EconomicEvent",
    "Impact",
    "KillSwitch",
    "KillSwitchMode",
    "KillSwitchState",
    "Mandate",
    "RateBreaker",
    "RiskContext",
    "RiskDecision",
    "RiskDecisionMade",
    "RiskInvariantError",
    "RiskKernel",
    "RiskLimits",
    "TradeProposal",
    "proposal_from_signal",
]
