"""Typed configuration for multi-agent-quant.

>>> from maq_core.config import load_config
>>> config = load_config("config")          # validates all seven files
>>> config.risk.trade_level.per_trade_risk
0.01
"""

from maq_core.config.limits import HARD_LIMITS, HardLimits
from maq_core.config.loader import (
    ENV_PREFIX,
    FILE_SECTIONS,
    SECTION_FILES,
    ConfigError,
    ConfigIssue,
    env_overrides,
    live_trading_issues,
    load_config,
    load_section,
)
from maq_core.config.models import SECTION_MODELS, AppConfig

__all__ = [
    "ENV_PREFIX",
    "FILE_SECTIONS",
    "HARD_LIMITS",
    "SECTION_FILES",
    "SECTION_MODELS",
    "AppConfig",
    "ConfigError",
    "ConfigIssue",
    "HardLimits",
    "env_overrides",
    "live_trading_issues",
    "load_config",
    "load_section",
]
