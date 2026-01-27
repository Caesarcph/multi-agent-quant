# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-agent quantitative trading system using 4 LLMs (GPT/Codex, Claude, Gemini, DeepSeek V3.2) as independent analysts. They generate trading signals from MT5 market snapshots, which are fused via weighted voting and executed on a MetaTrader 5 demo account. The system enforces three-layer risk management (trade/daily/account level) with a 50% max drawdown hard stop.

## Development Commands

```bash
# Install dependencies (Windows)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

# Set DeepSeek API key (uses NVIDIA endpoint)
setx NVIDIA_API_KEY "<your-key>"

# Run tests
.\.venv\Scripts\pytest

# Lint/format
.\.venv\Scripts\ruff check .
.\.venv\Scripts\black .

# Run single test file
.\.venv\Scripts\pytest tests/test_fusion_engine.py -v

# MT5 connectivity smoke test
.\.venv\Scripts\python scripts/mt5_smoke.py --count 5

# === MAIN ENTRY POINT: Full Hedge Fund Loop ===
.\.venv\Scripts\python scripts/run_loop.py

# Test with 1 cycle only
.\.venv\Scripts\python scripts/run_loop.py --max-cycles 1

# Change interval (default 15 min)
.\.venv\Scripts\python scripts/run_loop.py --interval 5

# === Legacy/Testing Scripts ===
# Single cycle (old mode, no trading dept)
.\.venv\Scripts\python scripts/run_cycle.py --agents deepseek

# Generate daily report
.\.venv\Scripts\python scripts/generate_report.py
```

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

### Key Modules

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
- `volume`: explicit lot size (used directly by executor)
- `risk.stop_loss_pips`, `risk.take_profit_pips`
- `context.regime`: trending_up | trending_down | ranging | volatile

### Configuration Files (config/)
- `tool_config.yaml`: MT5 path, snapshot settings, audit/logging toggles
- `model_config.yaml`: CLI commands for each agent
- `deepseek_config.yaml`: NVIDIA endpoint, rate limiting (40 RPM)
- `fusion_config.yaml`: Voting weights, agreement thresholds
- `risk_config.yaml`: Per-trade, daily, and account-level limits
- `execution_config.yaml`: Enable/disable live execution, dry_run mode

### Enabling Live Demo Execution
1. Set `config/execution_config.yaml`:
   - `enabled: true`
   - `dry_run: false`
2. Ensure MT5 terminal is running and logged into demo account

## Code Conventions

- Python 3.10+, 4-space indentation, `snake_case` functions/variables
- YAML/JSON config keys use `lower_snake_case`
- Tests in `tests/test_*.py`, mirroring module names
- MT5 integration tests require `MT5_INTEGRATION=1` env var

## Critical Constraints

- **Schema enforcement**: Non-conforming agent output is rejected; use `agents/json_utils.py` for robust parsing
- **Rate limits**: DeepSeek has 40 RPM; see `deepseek/rate_limiter.py`
- **Capital preservation**: Trading halts when equity drops to 50% of initial balance
- **Windows-only**: MT5 Python library requires Windows; CLI agents route through `cmd.exe`
