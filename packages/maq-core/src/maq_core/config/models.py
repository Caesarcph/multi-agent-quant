"""Typed schema for the YAML files in ``config/``.

Every model forbids unknown keys, so a typo such as ``enforcment:`` is reported
instead of silently falling back to a default. Defaults mirror what the legacy
code does when a key is absent (see ``maq_core.config.loader`` for how legacy
code keeps its own defaults).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maq_core.config.limits import HARD_LIMITS

Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
Action = Literal["BUY", "SELL", "HOLD"]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]

ResearchModel = Literal["gpt", "claude", "gemini", "deepseek"]
RESEARCH_MODELS: tuple[ResearchModel, ...] = ("gpt", "claude", "gemini", "deepseek")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


# =============================================================================
# tool_config.yaml
# =============================================================================
class Mt5Settings(StrictModel):
    terminal_path: str | None = None
    initialize_timeout_seconds: PositiveInt = 30
    max_bars: PositiveInt = 1000
    default_symbol: str = "EURUSD"


class BollingerSettings(StrictModel):
    period: PositiveInt = 20
    stddev: Annotated[float, Field(gt=0)] = 2.0


class IndicatorSettings(StrictModel):
    sma_periods: list[PositiveInt] = Field(default_factory=lambda: [20, 50])
    rsi_period: PositiveInt = 14
    atr_period: PositiveInt = 14
    adx_period: PositiveInt = 14
    bollinger: BollingerSettings = Field(default_factory=BollingerSettings)


class SnapshotSettings(StrictModel):
    symbol: str = "EURUSD"
    timeframe: Timeframe = "M1"
    bars: PositiveInt = 50
    include_symbol_universe: bool = False
    include_universe_ticks: bool = False
    include_positions: bool = False
    include_orders: bool = False
    include_indicators: bool = False
    include_correlations: bool = False
    indicators: IndicatorSettings = Field(default_factory=IndicatorSettings)
    symbol_universe: list[str] = Field(default_factory=list)
    symbol_limit: PositiveInt | None = None


class ScanSettings(StrictModel):
    interval_minutes: PositiveInt = 15
    include_open_positions: bool = True
    symbols: list[str] = Field(default_factory=list)
    symbol_limit: PositiveInt | None = None


ExternalFeed = Literal["economic_calendar", "news", "sentiment", "macro"]
ALL_FEEDS: tuple[ExternalFeed, ...] = ("economic_calendar", "news", "sentiment", "macro")


class ExternalDataSettings(StrictModel):
    enabled: bool = False
    plugins: list[str] = Field(default_factory=list)
    include: list[ExternalFeed] = Field(default_factory=lambda: list(ALL_FEEDS))
    days_ahead: NonNegativeInt = 7
    lookback_days: NonNegativeInt = 3
    max_items: PositiveInt | None = None
    symbol: str | None = None
    macro_series: list[str] | None = None

    @model_validator(mode="after")
    def _plugin_specs(self) -> Self:
        bad = [spec for spec in self.plugins if ":" not in spec]
        if bad:
            raise ValueError(f"plugins must look like 'package.module:ClassName', got {bad}")
        return self


class CorrelationSettings(StrictModel):
    enabled: bool = False
    symbols: list[str] = Field(default_factory=list)
    timeframe: Timeframe = "M15"
    bars: PositiveInt = 120


class LoggingSettings(StrictModel):
    directory: str = "logs"
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cycle_dir: str = "logs/cycles"


class AuditSettings(StrictModel):
    enabled: bool = False
    directory: str = "logs/agent_outputs"
    max_chars: PositiveInt = 4000


class StateFileSettings(StrictModel):
    enabled: bool = False
    path: str | None = None  # each consumer has its own default file name


ReportFormat = Literal["md", "json"]
REPORT_FORMATS: tuple[ReportFormat, ...] = ("md", "json")


class ReportSettings(StrictModel):
    directory: str = "reports/daily"
    formats: list[ReportFormat] = Field(default_factory=lambda: list(REPORT_FORMATS))


class DatabaseSettings(StrictModel):
    enabled: bool = False
    path: str = "logs/analytics.db"


class ToolCallSettings(StrictModel):
    enabled: bool = False
    max_iterations: PositiveInt = 2
    include_tools_in_prompt: bool = True
    max_tools: PositiveInt = 12
    max_result_chars: PositiveInt = 4000


class ToolConfig(StrictModel):
    """tool_config.yaml"""

    mt5: Mt5Settings = Field(default_factory=Mt5Settings)
    snapshot: SnapshotSettings = Field(default_factory=SnapshotSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    external_data: ExternalDataSettings = Field(default_factory=ExternalDataSettings)
    correlation: CorrelationSettings = Field(default_factory=CorrelationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    daily_state: StateFileSettings = Field(default_factory=StateFileSettings)
    account_state: StateFileSettings = Field(default_factory=StateFileSettings)
    report: ReportSettings = Field(default_factory=ReportSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    tool_calls: ToolCallSettings = Field(default_factory=ToolCallSettings)


# =============================================================================
# model_config.yaml
# =============================================================================
class CliDefaults(StrictModel):
    timeout_seconds: PositiveInt = 120
    args: list[str] = Field(default_factory=list)
    response_json_field: str | None = None


class CliAgentSettings(StrictModel):
    enabled: bool = False
    command: str | None = None
    args: list[str] | None = None
    timeout_seconds: PositiveInt | None = None
    response_json_field: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _command_when_enabled(self) -> Self:
        if self.enabled and not self.command:
            raise ValueError("an enabled CLI agent needs a `command`")
        return self


class CliSettings(StrictModel):
    defaults: CliDefaults = Field(default_factory=CliDefaults)
    gpt: CliAgentSettings = Field(default_factory=CliAgentSettings)
    claude: CliAgentSettings = Field(default_factory=CliAgentSettings)
    gemini: CliAgentSettings = Field(default_factory=CliAgentSettings)


class ModelConfig(StrictModel):
    """model_config.yaml (CLI research agents)"""

    cli: CliSettings = Field(default_factory=CliSettings)


# =============================================================================
# deepseek_config.yaml
# =============================================================================
class ApiSettings(StrictModel):
    base_url: str = ""
    model: str = "deepseek"
    api_key_env: str = ""


class SamplingSettings(StrictModel):
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    top_p: Fraction | None = None
    max_tokens: PositiveInt | None = None
    thinking: bool = False
    stream: bool = False


class RepairSettings(SamplingSettings):
    enabled: bool = True
    max_chars: PositiveInt = 8000


class RateLimitSettings(StrictModel):
    requests_per_minute: NonNegativeInt = 0
    safety_margin: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0


class DeepSeekLoggingSettings(StrictModel):
    enabled: bool = False
    directory: str = "logs/deepseek"
    max_chars: PositiveInt = 4000


class ChallengeAgentSettings(StrictModel):
    enabled: bool = False
    apply_result: bool = False
    allow_override: bool = False
    reduction_factor: Fraction = 0.5
    min_agreement_ratio: Fraction = 1.0
    trigger_on_regime_disagreement: bool = True


class EnabledFlag(StrictModel):
    enabled: bool = False


class RegimeIndicatorPeriods(StrictModel):
    adx: PositiveInt = 14
    atr: PositiveInt = 14
    bollinger_period: PositiveInt = 20
    bollinger_stddev: Annotated[float, Field(gt=0)] = 2.0


def _default_regime_timeframes() -> dict[Timeframe, int]:
    return {"H1": 100, "H4": 50, "D1": 20}


class RegimeAnalysisSettings(StrictModel):
    enabled: bool = False
    trigger_on_unclear: bool = True
    timeframes: dict[Timeframe, PositiveInt] = Field(default_factory=_default_regime_timeframes)
    indicator_periods: RegimeIndicatorPeriods = Field(default_factory=RegimeIndicatorPeriods)


class AuxiliarySettings(StrictModel):
    challenge_agent: ChallengeAgentSettings = Field(default_factory=ChallengeAgentSettings)
    daily_summary: EnabledFlag = Field(default_factory=EnabledFlag)
    regime_analysis: RegimeAnalysisSettings = Field(default_factory=RegimeAnalysisSettings)


class DeepSeekConfig(StrictModel):
    """deepseek_config.yaml (DeepSeek research agent, trading agent, auxiliary tasks)"""

    enabled: bool = False
    api: ApiSettings = Field(default_factory=ApiSettings)
    request_defaults: SamplingSettings = Field(default_factory=SamplingSettings)
    repair: RepairSettings = Field(default_factory=RepairSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    logging: DeepSeekLoggingSettings = Field(default_factory=DeepSeekLoggingSettings)
    auxiliary: AuxiliarySettings = Field(default_factory=AuxiliarySettings)

    @model_validator(mode="after")
    def _api_when_enabled(self) -> Self:
        if self.enabled:
            missing = [k for k in ("base_url", "api_key_env") if not getattr(self.api, k)]
            if missing:
                raise ValueError(f"api.{' and api.'.join(missing)} must be set when enabled")
        return self


# =============================================================================
# fusion_config.yaml
# =============================================================================
class RegimeAgreementSettings(StrictModel):
    required: bool = False
    min_agreement_ratio: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0
    fallback_action: Action = "HOLD"


class VotingThresholds(StrictModel):
    strong_buy: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    buy: Annotated[float, Field(gt=0.0, le=1.0)] = 0.35
    sell: Annotated[float, Field(ge=-1.0, lt=0.0)] = -0.35
    strong_sell: Annotated[float, Field(ge=-1.0, lt=0.0)] = -0.5

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        if not (self.strong_sell <= self.sell < 0 < self.buy <= self.strong_buy):
            raise ValueError("thresholds must satisfy strong_sell <= sell < 0 < buy <= strong_buy")
        return self


class VotingSettings(StrictModel):
    default_weights: dict[str, Annotated[float, Field(ge=0.0)]] = Field(default_factory=dict)
    thresholds: VotingThresholds = Field(default_factory=VotingThresholds)


class DynamicWeightingSettings(StrictModel):
    enabled: bool = False
    weights_path: str = "logs/dynamic_weights.json"
    min_weight: Fraction = 0.10
    max_weight: Fraction = 0.40
    window_signals: PositiveInt = 200
    min_samples: PositiveInt = 10
    timeframe: Timeframe = "M1"
    default_ttl_minutes: PositiveInt = 15

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.min_weight > self.max_weight:
            raise ValueError("min_weight must not exceed max_weight")
        return self


class FusionConfig(StrictModel):
    """fusion_config.yaml"""

    models: list[ResearchModel] = Field(default_factory=lambda: list(RESEARCH_MODELS))
    regime_agreement: RegimeAgreementSettings = Field(default_factory=RegimeAgreementSettings)
    voting: VotingSettings = Field(default_factory=VotingSettings)
    minority_logging: EnabledFlag = Field(default_factory=EnabledFlag)
    dynamic_weighting: DynamicWeightingSettings = Field(default_factory=DynamicWeightingSettings)

    @model_validator(mode="after")
    def _weights_match_models(self) -> Self:
        unknown = sorted(set(self.voting.default_weights) - set(self.models))
        if unknown:
            raise ValueError(f"voting.default_weights has models not listed in `models`: {unknown}")
        return self


# =============================================================================
# risk_config.yaml
# =============================================================================
class TradeLevelRisk(StrictModel):
    per_trade_risk: Annotated[float, Field(gt=0.0, le=HARD_LIMITS.max_per_trade_risk)] | None = None
    require_stop_loss: bool = False


class DailyLevelRisk(StrictModel):
    # Loss limit and profit target are fractions of the balance at the start of
    # the day (0.03 = 3%); max_daily_volume is in lots.
    daily_loss_limit: (
        Annotated[float, Field(gt=0.0, le=HARD_LIMITS.max_daily_loss_limit)] | None
    ) = None
    max_daily_trades: PositiveInt | None = None
    daily_profit_target: Annotated[float, Field(gt=0.0, le=1.0)] | None = None
    max_daily_volume: Annotated[float, Field(gt=0.0)] | None = None


class CapitalPreservation(StrictModel):
    enabled: bool = False
    trigger_drawdown: Fraction = 0.5
    resume_drawdown: Fraction = 0.3

    @model_validator(mode="after")
    def _hysteresis(self) -> Self:
        if self.resume_drawdown >= self.trigger_drawdown:
            raise ValueError("resume_drawdown must be below trigger_drawdown")
        return self


class AccountLevelRisk(StrictModel):
    max_drawdown: Annotated[float, Field(gt=0.0, le=HARD_LIMITS.max_account_drawdown)] | None = None
    max_concurrent_positions: PositiveInt | None = None
    max_positions_per_symbol: PositiveInt | None = None
    max_same_direction: PositiveInt | None = None
    max_correlation: Annotated[float, Field(gt=0.0, le=1.0)] | None = None
    # drawdown level -> size multiplier, e.g. {0.10: 0.75, 0.25: 0.5}
    drawdown_throttle: dict[
        Annotated[float, Field(gt=0.0, lt=1.0)], Annotated[float, Field(ge=0.0, le=1.0)]
    ] = Field(default_factory=dict)
    capital_preservation: CapitalPreservation = Field(default_factory=CapitalPreservation)

    @model_validator(mode="after")
    def _throttle_tightens(self) -> Self:
        multipliers = [self.drawdown_throttle[k] for k in sorted(self.drawdown_throttle)]
        if any(later > earlier for earlier, later in pairwise(multipliers)):
            raise ValueError("drawdown_throttle multipliers must not grow as drawdown deepens")
        return self


class EnforcementSettings(StrictModel):
    enabled: bool = True


class RiskConfig(StrictModel):
    """risk_config.yaml"""

    trade_level: TradeLevelRisk = Field(default_factory=TradeLevelRisk)
    daily_level: DailyLevelRisk = Field(default_factory=DailyLevelRisk)
    account_level: AccountLevelRisk = Field(default_factory=AccountLevelRisk)
    enforcement: EnforcementSettings = Field(default_factory=EnforcementSettings)


# =============================================================================
# execution_config.yaml
# =============================================================================
class PositionManagementSettings(StrictModel):
    enabled: bool = False
    close_on_opposite: bool = True
    allow_reverse: bool = True
    allow_scale_in: bool = False
    update_stops_on_same_direction: bool = True
    update_stops_on_hold: bool = False
    sl_tp_reference: Literal["current", "entry"] = "current"


class ExecutionConfig(StrictModel):
    """execution_config.yaml"""

    enabled: bool = False
    dry_run: bool = True
    use_signal_volume: bool = False
    use_risk_sizing: bool = True
    default_volume: Annotated[float, Field(gt=0.0)] | None = None
    magic: NonNegativeInt = 0
    comment: Annotated[str, Field(max_length=31)] = "multi-agent-quant"  # MT5 limit
    deviation: NonNegativeInt = 20
    max_spread_pips: Annotated[float, Field(gt=0.0)] | None = None
    require_tick_bid_ask: bool = True
    min_session_deals: NonNegativeInt | None = None
    min_session_volume: Annotated[float, Field(ge=0.0)] | None = None
    enforce_safety: bool = True
    position_management: PositionManagementSettings = Field(
        default_factory=PositionManagementSettings
    )


# =============================================================================
# validation_config.yaml
# =============================================================================
class PaperTradingSettings(StrictModel):
    enabled: bool = False
    min_trades: PositiveInt = 50
    state_path: str = "logs/paper_state.json"
    initial_balance: Annotated[float, Field(gt=0.0)] | None = None
    allow_scale_in: bool = False
    allow_reverse: bool = True


class GoLiveCriteria(StrictModel):
    min_win_rate: Fraction | None = None
    min_profit_factor: Annotated[float, Field(gt=0.0)] | None = None
    max_drawdown: Fraction | None = None
    min_sharpe_ratio: float | None = None


class PerformanceTrackingSettings(StrictModel):
    timeframe: Timeframe = "M1"
    default_ttl_minutes: PositiveInt = 15
    window_signals: PositiveInt = 500
    min_samples: PositiveInt = 10


class Mt5BacktestSettings(StrictModel):
    use_final_signal: bool = True
    default_ttl_minutes: PositiveInt = 15
    price_multiplier: Annotated[float, Field(gt=0.0)] = 1.0
    initial_balance: Annotated[float, Field(ge=0.0)] = 0.0
    models: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


class TradeAttributionSettings(StrictModel):
    use_final_signal: bool = True
    match_window_minutes: PositiveInt = 60
    comment_contains: str | None = None
    magic: NonNegativeInt | None = None


class ValidationConfig(StrictModel):
    """validation_config.yaml"""

    paper_trading: PaperTradingSettings = Field(default_factory=PaperTradingSettings)
    go_live_criteria: GoLiveCriteria = Field(default_factory=GoLiveCriteria)
    performance_tracking: PerformanceTrackingSettings = Field(
        default_factory=PerformanceTrackingSettings
    )
    mt5_backtest: Mt5BacktestSettings = Field(default_factory=Mt5BacktestSettings)
    trade_attribution: TradeAttributionSettings = Field(default_factory=TradeAttributionSettings)


# =============================================================================
# all files together
# =============================================================================
class AppConfig(StrictModel):
    """All configuration files of one config directory."""

    tool: ToolConfig = Field(default_factory=ToolConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)


SECTION_MODELS: dict[str, type[StrictModel]] = {
    "tool": ToolConfig,
    "model": ModelConfig,
    "deepseek": DeepSeekConfig,
    "fusion": FusionConfig,
    "risk": RiskConfig,
    "execution": ExecutionConfig,
    "validation": ValidationConfig,
}
