# Progress Log

## Checkpoint 0001
- Date: 2026-01-21 18:47
- Scope: Project scaffold and MT5 smoke entrypoint
- Completed:
  - Created core directories for orchestrator, tools, configs, and modules.
  - Added minimal MT5 connection + account info fetch in `orchestrator/main.py`.
  - Added baseline YAML configs and Python requirements.
  - Added logging setup and config loader utilities.
- Files:
  - requirements.txt
  - requirements-dev.txt
  - .gitignore
  - orchestrator/main.py
  - orchestrator/state.py
  - tools/config_loader.py
  - tools/logging_setup.py
  - tools/mt5_connection.py
  - tools/mt5_tools.py
  - config/tool_config.yaml
  - config/model_config.yaml
  - config/deepseek_config.yaml
  - config/fusion_config.yaml
  - config/risk_config.yaml
  - config/validation_config.yaml
- Next:
  - Add more MT5 data tools (rates, ticks, symbol info) and a CLI smoke check.
  - Decide on initial test runner wiring and add a simple test.

## Checkpoint 0002
- Date: 2026-01-21 18:47
- Scope: Execution path reliability
- Completed:
  - Ensured `orchestrator/main.py` can import project modules when run as a script.
- Files:
  - orchestrator/main.py
- Next:
  - Continue MT5 tool expansion and add a CLI smoke test script.

## Checkpoint 0003
- Date: 2026-01-21 18:52
- Scope: Environment setup and MT5 smoke check
- Completed:
  - Created `.venv` and installed base/dev dependencies.
  - Updated `requirements.txt` to a MetaTrader5 build compatible with Python 3.14.
  - Verified MT5 connection via `orchestrator/main.py`.
- Files:
  - requirements.txt
  - PROGRESS.md
- Next:
  - Expand MT5 tools (symbol info, rates, ticks) and add a CLI smoke script.

## Checkpoint 0004
- Date: 2026-01-21 18:59
- Scope: MT5 data tools + smoke script
- Completed:
  - Expanded MT5 helpers with symbol info, rates, ticks, and timeframe resolution.
  - Added CLI smoke script at `scripts/mt5_smoke.py`.
  - Added optional MT5 integration test guarded by `MT5_INTEGRATION=1`.
  - Updated DeepSeek config to use NVIDIA integrate endpoint + env var name.
- Files:
  - tools/mt5_tools.py
  - scripts/mt5_smoke.py
  - tests/test_mt5_integration.py
  - config/tool_config.yaml
  - config/deepseek_config.yaml
  - PROGRESS.md
- Next:
  - Implement agent interface, JSON schema validation, and LLM adapters (CLI + DeepSeek via OpenAI client).
  - Add a minimal end-to-end cycle that collects signals and logs the fused decision.

## Checkpoint 0005
- Date: 2026-01-21 19:13
- Scope: Agent interfaces + schema validation + DeepSeek adapter
- Completed:
  - Added agent base class, prompt loader/builder, JSON extraction, and schema validation.
  - Implemented CLI agent wrapper for Codex/Claude/Gemini.
  - Implemented DeepSeek agent using OpenAI client with NVIDIA endpoint + streaming.
  - Added output schema and system prompt templates.
  - Added schema validation test.
  - Installed OpenAI dependency.
- Files:
  - agents/base_agent.py
  - agents/cli_agent.py
  - agents/deepseek_agent.py
  - agents/agent_loader.py
  - agents/json_utils.py
  - agents/prompt_loader.py
  - agents/prompt_builder.py
  - agents/schema_validator.py
  - agents/prompts/system_prompt.md
  - agents/prompts/output_schema.json
  - tests/test_schema_validation.py
  - requirements.txt
  - config/model_config.yaml
  - config/deepseek_config.yaml
  - PROGRESS.md
- Next:
  - Build a minimal cycle that collects agent signals and logs/fuses them.
  - Add initial fusion logic and risk guard stubs.

## Checkpoint 0006
- Date: 2026-01-21 19:19
- Scope: Minimal end-to-end cycle (snapshot -> agents -> fusion -> risk)
- Completed:
  - Added snapshot builder using MT5 account, symbol info, and recent bars.
  - Implemented simple majority fusion and a basic risk guard.
  - Added a single-cycle runner script (`scripts/run_cycle.py`).
  - Added fusion unit tests.
  - Extended tool/fusion configs with snapshot defaults and weights.
- Files:
  - data/market_snapshot.py
  - fusion/simple_fusion.py
  - risk/risk_guard.py
  - orchestrator/cycle.py
  - scripts/run_cycle.py
  - config/tool_config.yaml
  - config/fusion_config.yaml
  - tests/test_fusion_simple.py
  - PROGRESS.md
- Next:
  - Add execution stubs and logging hooks for order validation.
  - Wire basic persistence for cycle outputs (logs/report files).

## Checkpoint 0007
- Date: 2026-01-21 19:22
- Scope: Execution stubs + cycle persistence
- Completed:
  - Added MT5 execution stub with order request builder and dry-run guard.
  - Added order validation checks and a basic unit test.
  - Added cycle output persistence to `logs/cycles` with timestamped JSON.
  - Added execution configuration file and logging config for cycle storage.
- Files:
  - execution/mt5_executor.py
  - execution/order_validator.py
  - execution/__init__.py
  - data/cycle_store.py
  - orchestrator/cycle.py
  - scripts/run_cycle.py
  - config/execution_config.yaml
  - config/tool_config.yaml
  - tests/test_order_validator.py
  - tools/mt5_tools.py
  - PROGRESS.md
- Next:
  - Add order sizing from risk and symbol info.
  - Extend fusion logic with regime agreement and confidence thresholds.

## Checkpoint 0008
- Date: 2026-01-21 19:25
- Scope: Risk-based position sizing
- Completed:
  - Added position sizing utility using risk fraction, stop loss, and tick value.
  - Wired sizing into MT5 executor with notes for fallbacks.
  - Added sizing unit tests.
- Files:
  - risk/position_sizer.py
  - execution/mt5_executor.py
  - config/execution_config.yaml
  - tests/test_position_sizer.py
  - PROGRESS.md
- Next:
  - Enhance fusion logic (regime agreement, thresholds).
  - Add execution safety checks (max spread, symbol trade mode).

## Checkpoint 0009
- Date: 2026-01-21 19:27
- Scope: Fusion improvements (regime agreement + thresholds)
- Completed:
  - Added fusion engine with weighted score thresholds and regime agreement gating.
  - Wired orchestrator to use the new fusion engine.
  - Expanded fusion config with thresholds and fallback action.
  - Added fusion engine tests.
- Files:
  - fusion/fusion_engine.py
  - orchestrator/cycle.py
  - config/fusion_config.yaml
  - tests/test_fusion_engine.py
  - PROGRESS.md
- Next:
  - Add execution safety checks (spread limits, trade mode).
  - Add optional logging of agent raw outputs for audit.

## Checkpoint 0010
- Date: 2026-01-21 19:28
- Scope: Execution safety checks
- Completed:
  - Added safety checks for trade mode and spread limits.
  - Enforced configurable max spread in executor.
  - Added execution safety tests.
- Files:
  - execution/safety_checks.py
  - execution/mt5_executor.py
  - config/execution_config.yaml
  - tests/test_execution_safety.py
  - PROGRESS.md
- Next:
  - Add agent response logging (raw outputs) for audit.
  - Add min liquidity / session checks if needed.

## Checkpoint 0011
- Date: 2026-01-21 19:30
- Scope: Agent output auditing
- Completed:
  - Added audit logging helper for prompts, raw outputs, and reasoning.
  - Captured last prompt/response in CLI and DeepSeek agents.
  - Wired optional audit logging into cycle run with configurable directory.
- Files:
  - data/agent_audit.py
  - agents/cli_agent.py
  - agents/deepseek_agent.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - PROGRESS.md
- Next:
  - Add session/liquidity checks (e.g., trade_mode, volume filters).
  - Implement basic exposure limits and correlation guard.

## Checkpoint 0012
- Date: 2026-01-21 19:37
- Scope: Session/liquidity safety filters
- Completed:
  - Added liquidity checks for bid/ask validity and session thresholds.
  - Wired liquidity checks into executor with block-on-fail behavior.
  - Added liquidity tests.
- Files:
  - execution/safety_checks.py
  - execution/mt5_executor.py
  - config/execution_config.yaml
  - tests/test_liquidity_checks.py
  - PROGRESS.md
- Next:
  - Add exposure limits and correlation guard.
  - Add a minimal portfolio state tracker.

## Checkpoint 0013
- Date: 2026-01-21 19:40
- Scope: Exposure limits + portfolio state
- Completed:
  - Added exposure guard for max positions, per-symbol caps, and same-direction caps.
  - Added portfolio state loader from MT5 positions.
  - Integrated exposure guard into cycle (forces HOLD on violations).
  - Added exposure tests.
- Files:
  - risk/exposure_guard.py
  - data/portfolio_state.py
  - orchestrator/cycle.py
  - config/risk_config.yaml
  - tests/test_exposure_guard.py
  - PROGRESS.md
- Next:
  - Add correlation guard for cross-symbol exposure.
  - Add daily trade count + daily loss guard.

## Checkpoint 0014
- Date: 2026-01-21 19:41
- Scope: Correlation guard for exposure
- Completed:
  - Added correlation matrix computation and guard.
  - Added optional correlation build in cycle with config toggle.
  - Added correlation configuration and tests.
- Files:
  - risk/correlation_guard.py
  - data/correlation.py
  - data/correlation_builder.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - config/risk_config.yaml
  - requirements.txt
  - tests/test_correlation_guard.py
  - PROGRESS.md
- Next:
  - Add daily loss/trade limits.
  - Add logging of correlation metrics per cycle.

## Checkpoint 0015
- Date: 2026-01-21 19:43
- Scope: Daily risk limits
- Completed:
  - Added daily guard for max trades and daily loss limit.
  - Added MT5 deal history loader and daily stats computation.
  - Integrated daily limits into cycle and surfaced daily stats in output.
  - Added daily guard tests.
- Files:
  - risk/daily_guard.py
  - data/trade_history.py
  - orchestrator/cycle.py
  - config/risk_config.yaml
  - tests/test_daily_guard.py
  - PROGRESS.md
- Next:
  - Add lightweight metrics summary per cycle.
  - Consider persistence for daily stats.

## Checkpoint 0016
- Date: 2026-01-21 19:50
- Scope: Cycle metrics summary
- Completed:
  - Added per-cycle summary metrics (signal counts, risk note counts, execution status).
  - Wired summary into cycle output.
  - Added metrics summary tests.
- Files:
  - data/metrics.py
  - orchestrator/cycle.py
  - tests/test_metrics_summary.py
  - PROGRESS.md
- Next:
  - Persist daily stats to disk for continuity across restarts.
  - Add a consolidated report generator.

## Checkpoint 0017
- Date: 2026-01-21 19:52
- Scope: Daily stats persistence
- Completed:
  - Added daily state persistence to JSON.
  - Wired daily state updates into cycle output.
  - Added daily state tests.
- Files:
  - data/daily_state.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - tests/test_daily_state.py
  - PROGRESS.md
- Next:
  - Add consolidated report generator.
  - Extend config docs for operational toggles.

## Checkpoint 0018
- Date: 2026-01-21 19:57
- Scope: Consolidated report generator
- Completed:
  - Added reporting utilities to aggregate cycle logs and render Markdown reports.
  - Added `scripts/generate_report.py` to generate daily reports.
  - Added report config and tests.
- Files:
  - data/reporting.py
  - scripts/generate_report.py
  - config/tool_config.yaml
  - tests/test_reporting.py
  - PROGRESS.md
- Next:
  - Add a minimal runbook in README or a separate OPERATIONS.md.
  - Add optional JSON report output for dashboards.

## Checkpoint 0019
- Date: 2026-01-21 19:59
- Scope: Operations runbook + JSON reports
- Completed:
  - Added OPERATIONS.md runbook with setup, commands, and toggles.
  - Extended report generator to output JSON alongside Markdown.
  - Added report payload builder and tests.
- Files:
  - OPERATIONS.md
  - data/reporting.py
  - scripts/generate_report.py
  - config/tool_config.yaml
  - tests/test_reporting.py
  - PROGRESS.md
- Next:
  - Consider adding a README section pointing to OPERATIONS.md.
  - Add a simple health check CLI for MT5 + API keys.

## Checkpoint 0020
- Date: 2026-01-21 20:10
- Scope: OpenAI client compatibility fix
- Completed:
  - Pinned httpx to 0.27.2 to resolve OpenAI client "proxies" argument error.
- Files:
  - requirements.txt
  - PROGRESS.md
- Next:
  - Optional: consider Python 3.12 environment to avoid pydantic v1 warning.

## Checkpoint 0021
- Date: 2026-01-21 20:24
- Scope: Robust JSON parsing for model outputs
- Completed:
  - Added normalization to recover from common JSON formatting issues (trailing commas, Python literals).
  - Added comment line stripping in JSON normalization.
- Files:
  - agents/json_utils.py
  - PROGRESS.md
- Next:
  - If parsing still fails, enable agent audit logs to inspect raw outputs.

## Checkpoint 0022
- Date: 2026-01-21 20:38
- Scope: Better JSON parsing + audit on failures
- Completed:
  - Improved JSON extraction by matching the first complete object.
  - Added audit logging even on agent failures.
  - Added error field to audit payloads.
- Files:
  - agents/json_utils.py
  - data/agent_audit.py
  - orchestrator/cycle.py
  - PROGRESS.md
- Next:
  - Inspect agent outputs if parsing still fails.

## Checkpoint 0023
- Date: 2026-01-21 20:44
- Scope: Strip non-ASCII junk outside JSON strings
- Completed:
  - Added sanitizer to remove non-ASCII characters outside strings before JSON parsing.
- Files:
  - agents/json_utils.py
  - PROGRESS.md
- Next:
  - If parsing still fails, add model retry with strict JSON-only prompt.

## Checkpoint 0024
- Date: 2026-01-21 20:48
- Scope: DeepSeek JSON repair fallback
- Completed:
  - Added optional repair pass to reformat invalid model output into strict JSON.
  - Added repair settings to DeepSeek config.
  - Logged repair outputs in audit logs.
- Files:
  - agents/deepseek_agent.py
  - agents/agent_loader.py
  - config/deepseek_config.yaml
  - data/agent_audit.py
  - orchestrator/cycle.py
  - PROGRESS.md
- Next:
  - If repair still fails, add a strict JSON-only retry with response_format (if supported).

## Checkpoint 0025
- Date: 2026-01-21 20:50
- Scope: Fix fusion call signature
- Completed:
  - Updated cycle to pass full fusion config into new fusion engine.
- Files:
  - orchestrator/cycle.py
  - PROGRESS.md
- Next:
  - Re-run cycle after repair.

## Checkpoint 0026
- Date: 2026-01-21 20:53
- Scope: Fix execution config YAML
- Completed:
  - Removed invalid YAML docstring from execution_config.yaml.
- Files:
  - config/execution_config.yaml
  - PROGRESS.md
- Next:
  - Re-run cycle.

## Checkpoint 0027
- Date: 2026-01-21 22:39
- Scope: Open limits + LLM-controlled volume/symbol context
- Completed:
  - Added LLM-controlled `volume` to schema and prompt.
  - Disabled risk enforcement by default; added enforcement toggle.
  - Added optional symbol universe/tick context in snapshot.
  - Added executor option to use signal volume directly and disable safety checks.
  - Forced agent output `model` to agent name for consistency.
- Files:
  - agents/prompts/output_schema.json
  - agents/prompts/system_prompt.md
  - tests/test_schema_validation.py
  - data/market_snapshot.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - config/risk_config.yaml
  - execution/mt5_executor.py
  - execution/order_validator.py
  - config/execution_config.yaml
  - tests/test_order_validator.py
  - agents/cli_agent.py
  - agents/deepseek_agent.py
  - PROGRESS.md
- Next:
  - Re-run cycle to confirm volume handling and open-mode behavior.

## Checkpoint 0028
- Date: 2026-01-21 22:51
- Scope: Windows CLI execution fix
- Completed:
  - Routed CLI agent calls through cmd.exe on Windows to resolve .cmd binaries.
- Files:
  - agents/cli_agent.py
  - PROGRESS.md
- Next:
  - Re-run full multi-agent cycle.

## Checkpoint 0029
- Date: 2026-01-21 22:56
- Scope: Non-interactive CLI adapters
- Completed:
  - Added CLI output file support and JSON-field extraction.
  - Configured codex/claude/gemini with non-interactive args.
- Files:
  - agents/cli_agent.py
  - agents/agent_loader.py
  - config/model_config.yaml
  - PROGRESS.md
- Next:
  - Re-run multi-agent cycle; adjust CLI args if needed per tool.

## Checkpoint 0030
- Date: 2026-01-22 00:03
- Scope: Portfolio scan + position management
- Completed:
  - Added optional indicators, positions, and orders into market snapshots.
  - Added MT5 tool helpers for positions/orders and indicator calculations.
  - Implemented position management (close/reverse/modify SL/TP) gated by config.
  - Added multi-symbol scan runner and 15-minute scheduler scripts.
  - Added scan settings to tool config and position-management settings to execution config.
- Files:
  - tools/indicators.py
  - tools/mt5_tools.py
  - data/market_snapshot.py
  - data/portfolio_state.py
  - orchestrator/cycle.py
  - orchestrator/scan.py
  - execution/mt5_executor.py
  - agents/prompts/system_prompt.md
  - config/tool_config.yaml
  - config/execution_config.yaml
  - scripts/run_portfolio.py
  - scripts/run_loop.py
  - PROGRESS.md
- Next:
  - Decide whether to enable position management in `config/execution_config.yaml`.
  - Run `scripts/run_portfolio.py` or `scripts/run_loop.py` to scan multiple symbols.

## Checkpoint 0031
- Date: 2026-01-22 10:38
- Scope: External data plugin interface + snapshot correlations
- Completed:
  - Added external data plugin interface with loader and aggregator utilities.
  - Enabled optional external data injection into snapshots with error tracking.
  - Added snapshot correlation inclusion toggle (reuses correlation builder).
  - Documented external data config defaults and prompt hints.
- Files:
  - tools/external_plugins.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - agents/prompts/system_prompt.md
  - PROGRESS.md
- Next:
  - Implement at least one concrete external plugin (e.g., economic calendar).
  - Add correlation tools exposure to agents if needed.

## Checkpoint 0032
- Date: 2026-01-22 10:51
- Scope: Account-level risk controls
- Completed:
  - Added account state tracking with drawdown metrics and capital preservation state.
  - Implemented account-level risk guard (drawdown throttle, max drawdown, preservation mode).
  - Extended daily guard to track volume and profit target limits.
  - Added minority opinion logging output from fusion engine.
  - Updated configs with account state path and new risk settings.
  - Added tests for account state and account guard.
- Files:
  - data/account_state.py
  - risk/account_guard.py
  - risk/daily_guard.py
  - orchestrator/cycle.py
  - fusion/fusion_engine.py
  - config/tool_config.yaml
  - config/risk_config.yaml
  - config/fusion_config.yaml
  - tests/test_account_state.py
  - tests/test_account_guard.py
  - tests/test_daily_guard.py
  - PROGRESS.md
- Next:
  - Add a concrete external data plugin implementation.
  - Wire performance tracking to drive dynamic weighting.

## Checkpoint 0033
- Date: 2026-01-22 11:05
- Scope: DeepSeek auxiliary agents
- Completed:
  - Added DeepSeek client wrapper with rate limiting and audit logging.
  - Implemented challenge agent, daily analyst, and regime analyst helpers.
  - Added multi-timeframe regime data collection with ADX and Bollinger width.
  - Integrated optional challenge review and regime analysis into cycle outputs.
  - Added scripts for daily summary and regime analysis runs.
  - Extended configs to cover auxiliary DeepSeek tasks and indicator settings.
- Files:
  - deepseek/__init__.py
  - deepseek/client.py
  - deepseek/rate_limiter.py
  - deepseek/utils.py
  - deepseek/challenge_agent.py
  - deepseek/daily_analyst.py
  - deepseek/regime_analyst.py
  - tools/indicators.py
  - orchestrator/cycle.py
  - config/deepseek_config.yaml
  - config/tool_config.yaml
  - scripts/run_daily_summary.py
  - scripts/run_regime_analysis.py
  - PROGRESS.md
- Next:
  - Add a concrete external data plugin implementation.
  - Implement validation modules (paper trader, backtester, go-live checks).

## Checkpoint 0034
- Date: 2026-01-22 11:06
- Scope: External data plugin example
- Completed:
  - Added local JSON external data plugin for calendar/news/sentiment/macro feeds.
  - Documented example plugin path in tool config.
- Files:
  - plugins/__init__.py
  - plugins/local_json_plugin.py
  - config/tool_config.yaml
  - PROGRESS.md
- Next:
  - Implement validation modules (paper trader, backtester, go-live checks).
  - Add performance tracking for dynamic weighting.

## Checkpoint 0035
- Date: 2026-01-22 11:11
- Scope: Validation modules
- Completed:
  - Added paper trader simulation with persistent state and basic SL/TP handling.
  - Added performance metric calculator and go-live criteria evaluator.
  - Added lightweight signal backtester for replay tests.
  - Added scripts for paper trading cycles and go-live checks.
  - Extended validation config with paper trading defaults.
- Files:
  - validation/__init__.py
  - validation/paper_trader.py
  - validation/performance_tracker.py
  - validation/go_live_checker.py
  - validation/backtester.py
  - scripts/run_paper_cycle.py
  - scripts/check_go_live.py
  - config/validation_config.yaml
  - PROGRESS.md
- Next:
  - Add performance tracking for dynamic weighting.
  - Implement a concrete backtest runner against MT5 historical data.

## Checkpoint 0036
- Date: 2026-01-22 11:16
- Scope: Dynamic weighting
- Completed:
  - Added dynamic weighting evaluator using MT5 future price checks.
  - Integrated optional dynamic weights into fusion engine.
  - Added script to update weights from recent cycle logs.
  - Added tests for weight normalization.
- Files:
  - fusion/dynamic_weighting.py
  - fusion/fusion_engine.py
  - config/fusion_config.yaml
  - scripts/update_dynamic_weights.py
  - tools/mt5_tools.py
  - tests/test_dynamic_weighting.py
  - PROGRESS.md
- Next:
  - Implement MT5 historical backtest runner for signal replay.
  - Add per-model performance tracking based on executed trades.

## Checkpoint 0037
- Date: 2026-01-22 11:27
- Scope: Model performance tracking
- Completed:
  - Added MT5-based model performance evaluation (by model/regime).
  - Added performance report generator script.
  - Updated validation config to include performance tracking settings.
  - Wired dynamic weighting to use the new model performance evaluator.
- Files:
  - validation/model_performance.py
  - scripts/generate_performance_report.py
  - config/validation_config.yaml
  - fusion/dynamic_weighting.py
  - PROGRESS.md
- Next:
  - Implement MT5 historical backtest runner for signal replay.
  - Add executed-trade attribution (link signals to realized MT5 deals).

## Checkpoint 0038
- Date: 2026-01-22 11:28
- Scope: MT5 historical backtest runner
- Completed:
  - Added MT5-backed signal replay backtest module.
  - Added script to run MT5 backtests from cycle logs.
  - Extended validation config with MT5 backtest defaults.
- Files:
  - validation/mt5_backtest.py
  - scripts/run_mt5_backtest.py
  - config/validation_config.yaml
  - PROGRESS.md
- Next:
  - Add executed-trade attribution (link signals to realized MT5 deals).

## Checkpoint 0039
- Date: 2026-01-22 11:30
- Scope: Executed trade attribution
- Completed:
  - Added MT5 deal attribution to prior signals with per-model summary.
  - Added script to generate trade attribution reports.
  - Extended validation config with attribution settings.
- Files:
  - validation/trade_attribution.py
  - scripts/attribute_trades.py
  - tools/mt5_tools.py
  - config/validation_config.yaml
  - PROGRESS.md
- Next:
  - Build database persistence layer (optional) for long-term analytics.

## Checkpoint 0040
- Date: 2026-01-22 11:32
- Scope: SQLite analytics storage
- Completed:
  - Added SQLite persistence for cycles/signals/trades/performance/aux logs.
  - Wired optional database storage into the cycle runner via config.
  - Added database configuration to tool config.
- Files:
  - data/database.py
  - orchestrator/cycle.py
  - config/tool_config.yaml
  - PROGRESS.md
- Next:
  - Add automated export (CSV/JSON) for analytics database if needed.

## Checkpoint 0041
- Date: 2026-01-22 11:33
- Scope: Tool registry foundation
- Completed:
  - Added tool registry with callable MT5 data and indicator utilities.
- Files:
  - tools/tool_registry.py
  - PROGRESS.md
- Next:
  - Add tool-call prompt protocol and optional agent tool loop.

## Checkpoint 0042
- Date: 2026-01-22 11:37
- Scope: Optional tool-call loop
- Completed:
  - Added tool-call loop for CLI and DeepSeek agents (opt-in via config).
  - Implemented tool request protocol with MT5-backed execution.
  - Added tool-call settings to tool config.
- Files:
  - agents/tool_loop.py
  - agents/cli_agent.py
  - agents/deepseek_agent.py
  - agents/agent_loader.py
  - config/tool_config.yaml
  - PROGRESS.md
- Next:
  - Add tests for tool-call protocol.

## Checkpoint 0043
- Date: 2026-01-22 12:00
- Scope: Enhanced system prompt for stricter JSON output
- Completed:
  - Strengthened agent system prompt with explicit numeric value requirements.
  - Added clear rules for volume (must be decimal), confidence (0.0-1.0), pips (positive integers).
  - Added example volume values to prevent placeholder/variable name outputs.
- Files:
  - agents/prompts/system_prompt.md
  - PROGRESS.md
- Next:
  - Implement Research + Trading department separation.

## Checkpoint 0044
- Date: 2026-01-22 12:10
- Scope: Research + Trading department architecture
- Completed:
  - Created dedicated Trading Agent (`agents/trading_agent.py`) with full MT5 tool access.
  - Trading Agent can query symbol specs, account info, calculate margin and lot sizes.
  - Added new tools to registry: `calculate_margin`, `calculate_lot_size`.
  - Updated README.md with new architecture diagram showing department separation.
  - Research department focuses on direction/confidence, Trading department handles execution details.
- Files:
  - agents/trading_agent.py (NEW)
  - tools/tool_registry.py
  - README.md
  - PROGRESS.md

## Checkpoint 0045
- Date: 2026-01-22 12:30
- Scope: Full hedge fund refactor - unified main loop
- Completed:
  - Rewrote `scripts/run_loop.py` as the single main entry point.
  - Removed dry-run mode (demo account = always live).
  - Removed symbol restrictions - system scans entire market.
  - Market scanner prioritizes forex majors by spread and liquidity.
  - Research Department uses ALL 4 LLMs (GPT, Claude, Gemini, DeepSeek).
  - Failed agents are marked "on vacation" - system continues with remaining analysts.
  - Trading Department (DeepSeek) has full MT5 tool access for execution decisions.
  - Deleted redundant scripts (run_loop_v2.py, run_with_trading_dept.py).
- Files:
  - scripts/run_loop.py (REWRITTEN)
  - scripts/run_loop_v2.py (DELETED)
  - scripts/run_with_trading_dept.py (DELETED)
  - PROGRESS.md
- Architecture:
  ```
  Market Scanner → Select top 20 symbols by liquidity/spread
         ↓
  Research Dept (4 LLMs) → Each analyst votes independently
         ↓                  (failed = "on vacation")
  Fusion Engine → Weighted voting, regime agreement
         ↓
  Trading Dept (1 LLM) → Query MT5 tools → EXECUTE/REJECT
         ↓
  MT5 Executor → Place order on demo account
  ```
- Next:
  - Test full cycle with all 4 agents.
  - Add daily summary report generation.
  - Consider adding more asset classes to scanner (indices, commodities).

## Checkpoint 0046
- Date: 2026-01-22 15:21
- Scope: Trading execution alignment + run_loop safety context
- Completed:
  - Added DeepSeek chat completion support for the Trading Department agent.
  - Allowed price-based SL/TP in order validation/execution and risk guard checks.
  - Updated run_loop to refresh account context, apply optional risk guards, and emit standard cycle metadata.
  - Injected external data plugins into research snapshots when enabled.
  - Fixed fusion score logging and confidence handoff; added required entry fields in execution.
  - Added test coverage for price-based SL/TP validation.
- Files:
  - deepseek/client.py
  - execution/order_validator.py
  - execution/mt5_executor.py
  - risk/risk_guard.py
  - scripts/run_loop.py
  - tests/test_order_validator.py
  - PROGRESS.md
- Next:
  - Add tool-loop tests and optional self-consistency sampling.
  - Consider wiring DeepSeek auxiliary agents into run_loop.

## Checkpoint 0047
- Date: 2026-01-22 22:34
- Scope: Trading decision reliability + explainable signals
- Completed:
  - Enforced a final decision after a limited number of tool calls in the Trading Agent.
  - Added explicit trade rationale fields (technical vs fundamental, horizon, SL/TP reason) to the research schema.
  - Updated system prompt and schema validation tests for the new rationale fields.
  - Ensured symbols are selected before MT5 rate retrieval to reduce data call failures.
- Files:
  - agents/trading_agent.py
  - agents/prompts/system_prompt.md
  - agents/prompts/output_schema.json
  - tests/test_schema_validation.py
  - tools/mt5_tools.py
  - PROGRESS.md
- Next:
  - Capture and display rationale in reports and cycle summaries.
  - Decide whether to expand horizon enums beyond short/long term.

## Checkpoint 0048
- Date: 2026-01-22 22:40
- Scope: Trading agent indentation fix
- Completed:
  - Re-indented `process_signal` so it stays within the TradingAgent class.
  - Verified module compiles without indentation errors.
- Files:
  - agents/trading_agent.py
  - PROGRESS.md
- Next:
  - Re-run run_loop to verify Trading Dept decisions execute.

## Checkpoint 0049
- Date: 2026-01-22 23:06
- Scope: Trading tool payload trimming + DeepSeek repair tightening
- Completed:
  - Limited Trading Dept tool results to avoid oversized prompts (summarized get_symbols, truncated large lists/JSON).
  - Strengthened DeepSeek JSON repair prompt to fix negative stop/take values.
- Files:
  - agents/trading_agent.py
  - agents/deepseek_agent.py
  - PROGRESS.md
- Next:
  - Re-run run_loop and confirm Trading Dept can reach EXECUTE without 400 errors.

## Checkpoint 0050
- Date: 2026-01-22 23:30
- Scope: MT5 execution reliability and rejection transparency
- Completed:
  - Added filling-mode fallback for order_send to avoid "Unsupported filling mode" rejections.
  - Classified MT5 retcodes so rejected orders no longer appear as submitted.
  - Recorded retry notes when switching filling modes.
- Files:
  - execution/mt5_executor.py
  - PROGRESS.md
- Next:
  - Re-run run_loop and confirm USDJPY order uses supported filling mode.
