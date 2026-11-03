# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-agent quantitative trading system using 4 LLMs (GPT/Codex, Claude, Gemini, DeepSeek V3.2) as independent analysts. They generate trading signals from MT5 market snapshots, which are fused via weighted voting and executed on a MetaTrader 5 demo account. The system enforces three-layer risk management (trade/daily/account level) with a 50% max drawdown hard stop.

## Development Commands

The project is managed with [uv](https://docs.astral.sh/uv/) (`uv.lock` is the source of truth).
Development and tests work on any OS; live trading needs Windows (MetaTrader5 package).

```bash
uv sync                                   # create .venv and install everything (incl. dev tools)

# Checks (the same ones CI runs)
uv run pytest                             # all tests; MT5 and LLM APIs are faked
uv run pytest tests/test_mt5_executor.py -v
uv run ruff check . && uv run ruff format .
uv run mypy                               # strict, for packages/
uv run maq config validate                # config/*.yaml + MAQ_* overrides
HYPOTHESIS_PROFILE=thorough uv run pytest packages/maq-risk   # property tests, 100k examples

# Environment and operations
uv run maq doctor                         # Python, deps, MT5, config, API key, CLI agents
uv run maq kill-switch engage --mode halt_new --reason "..."   # or flatten; release; status
uv run maq audit verify                   # check the hash chain of logs/events.db
setx NVIDIA_API_KEY "<your-key>"          # DeepSeek via the NVIDIA endpoint (Windows)
setx MAQ_TOOL__MT5__TERMINAL_PATH "C:/path/to/terminal64.exe"   # per-machine override

# MT5 connectivity smoke test
uv run python scripts/mt5_smoke.py --count 5

# === MAIN ENTRY POINT: Full Hedge Fund Loop ===
uv run python scripts/run_loop.py --max-cycles 1    # dry run: nothing is sent to MT5
uv run python scripts/run_loop.py --live            # real orders to the (demo) account
uv run python scripts/run_loop.py --interval 5      # change interval (default 15 min)

# === Legacy/Testing Scripts ===
uv run python scripts/run_cycle.py --agents deepseek    # single cycle, no trading dept
uv run python scripts/generate_report.py
```

pip still works: `pip install -r requirements.txt -r requirements-dev.txt` (both files are exported
from `uv.lock` by a pre-commit hook; do not edit them by hand).

## Architecture

### Two-Department Design
```
┌─────────────────────────────────────────────────────────────┐
│  RESEARCH DEPARTMENT (4 LLMs: GPT, Claude, Gemini, DeepSeek)│
│  Input: Market snapshot                                      │
│  Output: Direction (BUY/SELL/HOLD), confidence, regime       │
│  Note: Does NOT need to know contract specs                  │
└─────────────────────────────────────────────────────────────┘
                            ↓ Signal
┌─────────────────────────────────────────────────────────────┐
│  TRADING DEPARTMENT (1 DeepSeek Agent with MT5 tool access) │
│  Tools: get_symbol_info, get_account_info, calculate_margin,│
│         calculate_lot_size, get_positions                    │
│  Output: EXECUTE/REJECT/MODIFY + exact order parameters      │
└─────────────────────────────────────────────────────────────┘
                            ↓ Order
┌─────────────────────────────────────────────────────────────┐
│  MT5 EXECUTOR → Risk Guards → Place Order                   │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow (One Cycle)
```
MT5 Terminal → Market Snapshot → Research Dept (4 LLMs parallel)
    ↓
Fusion Engine (regime check + voting) → Trading Dept (1 LLM + tools)
    ↓
Risk Guards (trade/daily/account) → MT5 Executor
```

### New platform packages (2026 upgrade; strict mypy)

- **packages/maq-core**: domain types (`maq_core.domain`), event base + registry (`maq_core.events`), clocks, config schema, logging
- **packages/maq-brokers**: async `Broker` protocol, `PaperBroker`, `Mt5Broker` (MT5 adapter), `Mt5GatewayBroker` (HTTP client); `maq_brokers.testing.BrokerContract` is the contract suite every broker must pass
- **packages/maq-risk**: `RiskKernel.evaluate(proposal, context)` -- pure, deterministic pre-trade checks and sizing; `CircuitBreakers`, `KillSwitch`, `Mandate`; rules in `docs/adr/0008-risk-kernel-rules.md`
- **packages/maq-engine**: `EventStore` (append-only, hash-chained SQLite), `OrderManager` (persist before send, idempotency, reconciliation), `ExecutionPipeline` (proposal -> kernel -> orders)
- **services/mt5-gateway**: FastAPI service owning one MT5 terminal (Windows); REST + long-polled events, bearer token
- The legacy loop (below) still does the trading; it honours the kill switch. Phase 4 moves it onto the new packages.

### Key Modules (legacy)

- **orchestrator/cycle.py**: Main cycle logic orchestrating snapshot→agents→fusion→risk→execution
- **agents/**: LLM wrappers (CLI agents for GPT/Claude/Gemini, API client for DeepSeek)
- **fusion/fusion_engine.py**: 4-model weighted voting with regime agreement (3/4 threshold)
- **risk/**: Three-layer guards (risk_guard, daily_guard, account_guard, exposure_guard, correlation_guard)
- **execution/mt5_executor.py**: Order execution with safety checks (spread, liquidity, trade mode)
- **deepseek/**: Auxiliary DeepSeek tasks (challenge arbitration, daily summary, regime analysis)
- **validation/**: Paper trading, backtesting, go-live criteria checking

### Agent Output Schema
Agents must produce JSON matching `agents/prompts/output_schema.json`. Key fields:
- `action`: BUY | SELL | HOLD
- `confidence`: 0.0-1.0
- `volume`: proposed lot size -- advisory only: the executor sizes from the risk budget and the proposal can only shrink it
- `risk.stop_loss_pips`, `risk.take_profit_pips`
- `context.regime`: trending_up | trending_down | ranging | volatile

### Configuration Files (config/)
- `tool_config.yaml`: MT5 path, snapshot settings, audit/logging toggles
- `model_config.yaml`: CLI commands for each agent
- `deepseek_config.yaml`: NVIDIA endpoint, rate limiting (40 RPM)
- `fusion_config.yaml`: Voting weights, agreement thresholds
- `risk_config.yaml`: Per-trade, daily, and account-level limits
- `execution_config.yaml`: Enable/disable live execution, dry_run mode

All seven files are validated by the schema in `packages/maq-core/src/maq_core/config/` (unknown keys
are errors, risk values are capped by hard limits in `limits.py`). Any key can be overridden with an
environment variable `MAQ_<FILE>__<KEY>__<SUBKEY>`, e.g. `MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK=0.005`.
`tools/config_loader.load_yaml` applies the same validation for legacy code. When you add a key that
code reads, add it to the schema too.

### Enabling Live Demo Execution
- `scripts/run_loop.py`: pass `--live`. Without it every order is a dry run, whatever the config says.
- `scripts/run_cycle.py` / `orchestrator/cycle.py`: set `enabled: true` and `dry_run: false` in `config/execution_config.yaml`.
- Ensure MT5 terminal is running and logged into demo account.

### Safety defaults (do not loosen casually)
- `risk_config.yaml` `enforcement.enabled: true` -- with it off, the 50% drawdown stop, daily limits and exposure limits are skipped.
- `execution_config.yaml` `use_signal_volume: false`, `use_risk_sizing: true` -- lot size comes from the risk budget and stop distance; an LLM-proposed volume can only shrink it (`risk/position_sizer.py::size_order`).
- Downstream code must act on `fusion.fusion_engine.decision_signal(fused)`, not `fused["representative_signal"]`: the representative is one model's raw signal and can be BUY/SELL when the ensemble decided HOLD.
- `run_loop.py --live` refuses to start when `maq_core.config.live_trading_issues()` reports anything.
- The trading department may only EXECUTE/MODIFY the researched symbol and direction (`TradingAgent._validate_decision`); anything else becomes REJECT.
- Lot sizes are floored to `volume_step` (`execution/mt5_executor.py::_clamp_volume`), never rounded up past the risk budget.
- A `None` from `order_send` means "no answer", not "not executed": never resend (see `_send_with_filling_fallback`, `Mt5Broker`, `OrderManager`). Unknown outcomes are looked up with `find_order`.
- The risk kernel always requires a stop loss, rejects sizes below the minimum lot, and needs a valid mandate for real-money accounts. Do not weaken these to make a test pass.
- Every order goes through `OrderManager.submit` in new code: it writes the order to the event log before sending. Never call `broker.submit` directly from strategy code.
- `daily_loss_limit` / `daily_profit_target` are fractions of the day's starting balance (`risk/daily_guard.py`), not money.

## Code Conventions

- Python 3.11+, Ruff (lint + format, 100 columns), 4-space indentation, `snake_case` functions/variables
- New code goes into workspace packages under `packages/` (src layout, strict mypy); the top-level
  legacy packages (`agents/`, `execution/`, ...) are migrated gradually (see `docs/roadmap/`)
- YAML/JSON config keys use `lower_snake_case`
- Tests in `tests/test_*.py` (package tests in `packages/*/tests/`); every bug fix gets a test
- Test doubles: `tests/fakes/mt5.py` (fake MetaTrader5 terminal, use the `fake_mt5` fixture),
  `tests/fakes/mt5_server.py` (simulated MT5 trade server for adapter tests),
  `tests/fakes/llm_server.py` (OpenAI-compatible HTTP server); `tests/factories.py` has a schema-valid signal;
  `maq_risk.testing` builds risk contexts and proposals
- New broker implementations subclass `maq_brokers.testing.BrokerContract` and provide a `harness` fixture
- Async tests run with pytest-asyncio in auto mode (no marker needed)
- MT5 integration tests require `MT5_INTEGRATION=1` and a running terminal
- Architecture decisions are recorded in `docs/adr/`

## Critical Constraints

- **Schema enforcement**: Non-conforming agent output is rejected; use `agents/json_utils.py` for robust parsing
- **Rate limits**: DeepSeek has 40 RPM; see `deepseek/rate_limiter.py`
- **Capital preservation**: Trading halts when equity drops to 50% of initial balance
- **Windows for live trading**: the MetaTrader5 package only exists on Windows (dependency marker); everything else, including tests, runs on any OS. CLI agents route through `cmd.exe` on Windows
