# Multi-Agent LLM Quantitative Trading System

[![CI](https://github.com/Caesarcph/multi-agent-quant/actions/workflows/ci.yml/badge.svg)](https://github.com/Caesarcph/multi-agent-quant/actions/workflows/ci.yml)

A multi-agent quantitative trading system that uses four independent Large Language Models (GPT, Claude, Gemini, DeepSeek) as research analysts, fuses their signals via weighted ensemble voting, and executes trades on a MetaTrader 5 demo account through a dedicated trading department.

## Architecture

```
                    RESEARCH DEPARTMENT
  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
  │   GPT   │ │ Claude  │ │ Gemini  │ │DeepSeek │
  │  (CLI)  │ │  (CLI)  │ │  (CLI)  │ │  (API)  │
  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘
       └───────────┴──────────┴───────────┘
                       │
              ┌────────▼────────┐
              │  Fusion Engine  │  4-model weighted voting
              │  (Ensemble)     │  + regime agreement check
              └────────┬────────┘
                       │
                TRADING DEPARTMENT
              ┌────────▼────────┐
              │ Trading Agent   │  DeepSeek + MT5 tool access
              │ (Order Sizing)  │  EXECUTE / REJECT / MODIFY
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  Risk Guards    │  Trade → Daily → Account
              │  (3 Layers)     │  50% max drawdown hard stop
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  MT5 Executor   │  Demo account
              └─────────────────┘
```

### Two-Department Design

| Department | Role | Models | Access |
|---|---|---|---|
| **Research** | Market analysis, direction, regime detection | GPT, Claude, Gemini, DeepSeek | Read-only market snapshots |
| **Trading** | Order sizing, margin checks, execution decisions | DeepSeek (dedicated) | Full MT5 API (symbol info, account, margin calc) |

Research agents focus purely on market direction without needing to know contract specifications. The trading agent queries real-time symbol specs and account state to produce valid, executable orders. Failed agents are treated as "on vacation" and the system continues with remaining analysts.

## Features

- **4-Model Ensemble**: Independent LLMs analyze the same market snapshot without seeing each other's outputs
- **Full Market Freedom**: Scans all available symbols, no hardcoded symbol list
- **Regime-Aware Fusion**: Requires 3/4 model agreement on market regime before trading
- **Tool-Augmented Trading**: Trading agent queries MT5 APIs for contract specs, margin, lot sizing
- **Three-Layer Risk Management**: Per-trade limits, daily limits, account-level 50% drawdown hard stop
- **Graceful Degradation**: Failed agents are skipped; system operates with remaining analysts
- **Structured Output**: All agents produce JSON conforming to a strict schema; non-conforming output is rejected
- **Full Audit Trail**: Every cycle, signal, fusion result, and execution is logged

## Prerequisites

- **Windows** (required by MetaTrader5 Python library)
- **Python 3.10+**
- **MetaTrader 5 Terminal** installed and logged into a demo account
- **CLI Access** to at least one of: GPT/Codex CLI, Claude CLI, Gemini CLI
- **NVIDIA API Key** for DeepSeek V3.2 (free tier, 40 RPM)

## Installation

```bash
git clone https://github.com/Caesarcph/multi-agent-quant.git
cd multi-agent-quant

python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

### Environment Variables

```bash
# DeepSeek V3.2 via NVIDIA endpoint (required)
setx NVIDIA_API_KEY "<your-nvidia-api-key>"
```

### MT5 Configuration

Edit `config/tool_config.yaml` to set your MT5 terminal path:

```yaml
mt5:
  terminal_path: "C:\\Program Files\\MetaTrader 5 Terminal\\terminal64.exe"
```

Make sure your MT5 terminal is running and logged into a demo account before starting the system.

## Usage

### Run the Full Trading Loop

```bash
.\.venv\Scripts\python scripts\run_loop.py
```

This starts a continuous loop that:
1. Scans the market for tradeable symbols
2. Dispatches each symbol to all 4 research agents
3. Fuses signals via weighted voting
4. Sends actionable signals to the trading department
5. Executes approved orders on MT5
6. Repeats on a configurable interval

### Options

```bash
# Run 1 cycle only (testing)
.\.venv\Scripts\python scripts\run_loop.py --max-cycles 1

# Change interval (default: 15 minutes)
.\.venv\Scripts\python scripts\run_loop.py --interval 5

# Specify analysis timeframe
.\.venv\Scripts\python scripts\run_loop.py --timeframe M15
```

### Other Scripts

```bash
# MT5 connectivity smoke test
.\.venv\Scripts\python scripts\mt5_smoke.py --count 5

# Single legacy cycle (no trading department)
.\.venv\Scripts\python scripts\run_cycle.py --agents deepseek

# Generate daily performance report
.\.venv\Scripts\python scripts\generate_report.py

# Run backtesting
.\.venv\Scripts\python scripts\run_mt5_backtest.py

# Check go-live validation criteria
.\.venv\Scripts\python scripts\check_go_live.py
```

### Run Tests

```bash
.\.venv\Scripts\pytest

# Specific test file
.\.venv\Scripts\pytest tests\test_fusion_engine.py -v

# MT5 integration tests (requires running terminal)
set MT5_INTEGRATION=1
.\.venv\Scripts\pytest tests\test_mt5_integration.py -v
```

## Project Structure

```
multi-agent-quant/
├── agents/                     # LLM agent wrappers
│   ├── cli_agent.py            #   CLI wrapper (GPT/Claude/Gemini)
│   ├── deepseek_agent.py       #   DeepSeek API wrapper
│   ├── trading_agent.py        #   Trading department agent (MT5 tools)
│   ├── schema_validator.py     #   JSON schema enforcement
│   ├── json_utils.py           #   Robust JSON parsing
│   └── prompts/
│       ├── system_prompt.md    #   Shared system instructions
│       └── output_schema.json  #   Agent output JSON schema
│
├── fusion/                     # Signal fusion engine
│   ├── fusion_engine.py        #   4-model weighted voting + regime check
│   └── dynamic_weighting.py    #   Adaptive weight adjustment
│
├── risk/                       # Three-layer risk management
│   ├── risk_guard.py           #   Layer 1: per-trade validation
│   ├── daily_guard.py          #   Layer 2: daily limits
│   ├── account_guard.py        #   Layer 3: account survival (50% DD)
│   ├── exposure_guard.py       #   Portfolio exposure limits
│   └── correlation_guard.py    #   Cross-symbol correlation monitoring
│
├── execution/                  # Order execution
│   ├── mt5_executor.py         #   MT5 order execution with filling fallback
│   ├── order_validator.py      #   Pre-execution order validation
│   └── safety_checks.py        #   Spread, liquidity, margin checks
│
├── deepseek/                   # DeepSeek auxiliary features
│   ├── client.py               #   API client with rate limiting
│   ├── rate_limiter.py         #   40 RPM rate limiter
│   ├── challenge_agent.py      #   Disagreement arbitration
│   ├── daily_analyst.py        #   End-of-day summaries
│   └── regime_analyst.py       #   Deep regime analysis
│
├── data/                       # Data management & state
│   ├── market_snapshot.py      #   Market snapshot builder
│   ├── account_state.py        #   Account state tracking
│   ├── daily_state.py          #   Daily metrics aggregation
│   ├── portfolio_state.py      #   Position tracking
│   └── cycle_store.py          #   Cycle data persistence
│
├── tools/                      # Data access layer
│   ├── mt5_tools.py            #   MT5 data tools (rates, ticks, symbols)
│   ├── tool_registry.py        #   Tool dispatch for trading agent
│   ├── indicators.py           #   Technical indicator calculations
│   └── external_plugins.py     #   External data plugin interface
│
├── validation/                 # Testing & validation
│   ├── paper_trader.py         #   Paper trading simulator
│   ├── backtester.py           #   Historical backtesting
│   ├── performance_tracker.py  #   Live performance monitoring
│   └── go_live_checker.py      #   Go-live criteria validation
│
├── config/                     # Configuration (YAML)
│   ├── tool_config.yaml        #   MT5 path, snapshot settings
│   ├── model_config.yaml       #   CLI commands for each agent
│   ├── deepseek_config.yaml    #   DeepSeek API & rate limits
│   ├── fusion_config.yaml      #   Voting weights & thresholds
│   ├── risk_config.yaml        #   Risk limits (all 3 layers)
│   └── execution_config.yaml   #   Execution mode & safety
│
├── scripts/                    # Entry-point scripts
│   ├── run_loop.py             #   Main hedge fund loop
│   ├── run_cycle.py            #   Single cycle (legacy)
│   ├── mt5_smoke.py            #   MT5 connectivity test
│   └── generate_report.py      #   Performance reports
│
└── tests/                      # Test suite (16 modules)
    ├── test_fusion_engine.py
    ├── test_daily_guard.py
    ├── test_account_guard.py
    ├── test_schema_validation.py
    └── ...
```

## Configuration

All configuration is in YAML files under `config/`. Key settings:

### Risk Management (`risk_config.yaml`)

```yaml
trade_level:
  per_trade_risk: 0.01          # 1% of initial balance per trade
  max_position_size: 0.05       # 5% max single position

daily_level:
  daily_loss_limit: 0.03        # Stop trading at 3% daily loss
  max_daily_trades: 10

account_level:
  max_drawdown: 0.50            # HARD STOP at 50% drawdown
  max_concurrent_positions: 5
```

### Fusion Engine (`fusion_config.yaml`)

```yaml
voting:
  default_weights:
    gpt: 0.25
    claude: 0.25
    gemini: 0.25
    deepseek: 0.25
  thresholds:
    strong_buy: 0.50
    buy: 0.35
    sell: -0.35
    strong_sell: -0.50
```

### Agent CLI Commands (`model_config.yaml`)

Configure the CLI commands used to invoke each LLM agent. Each agent receives a market snapshot via stdin and must return structured JSON.

## How It Works

### One Trading Cycle

1. **Market Scan**: Scans all MT5 symbols, filters by spread/liquidity, selects top candidates
2. **Research Phase**: For each symbol, all 4 LLMs independently analyze the market snapshot
3. **Schema Validation**: Agent outputs are validated against `output_schema.json`; invalid outputs are discarded
4. **Signal Fusion**: Weighted voting with regime agreement check (need 3/4 models to agree on regime)
5. **Trading Department**: If signal is actionable (BUY/SELL), the trading agent queries MT5 for contract specs, calculates lot size, validates margin, and decides EXECUTE/REJECT/MODIFY
6. **Risk Guards**: Three layers of checks (trade/daily/account) with progressive position throttling
7. **Execution**: Order sent to MT5 with automatic filling mode fallback
8. **Logging**: Full cycle data saved for audit and research

### Agent Output Schema

Every agent must produce JSON matching this structure:

```json
{
  "model": "gpt|claude|gemini|deepseek",
  "action": "BUY|SELL|HOLD",
  "confidence": 0.65,
  "volume": 0.1,
  "risk": {
    "stop_loss_pips": 25,
    "take_profit_pips": 50,
    "risk_fraction_of_initial": 0.01
  },
  "context": {
    "regime": "trending_up|trending_down|ranging|volatile",
    "key_levels": { "support": [1.085], "resistance": [1.092] }
  },
  "reason_codes": ["MA_BULLISH_CROSS", "SUPPORT_BOUNCE"],
  "ttl_minutes": 60
}
```

### Fusion Logic

- Each model's vote: `score = action_value * confidence` (BUY=+1, HOLD=0, SELL=-1)
- Final score: `S = sum(weight_i * score_i)`
- Decision thresholds: `|S| >= 0.50` full size, `0.35 <= |S| < 0.50` reduced size, `|S| < 0.35` HOLD

## Research Applications

The system logs all data needed for research analysis:

- **Model Comparison**: Are 4-model ensembles better than individual models?
- **Confidence Calibration**: Do models' stated confidence levels correlate with actual outcomes?
- **Regime Detection**: Can LLMs reliably identify market regimes?
- **Disagreement as Signal**: Does inter-model disagreement predict volatility?
- **Minority Opinion Tracking**: Are dissenting models systematically right or wrong?
- **Dynamic Weighting**: Does performance-based weight adaptation improve returns?

## Limitations

- **Windows-only**: The MetaTrader5 Python library only runs on Windows
- **CLI Agent Reliability**: GPT, Claude, and Gemini CLI sessions may have encoding issues or session timeouts on long runs; DeepSeek via API is the most reliable agent
- **Demo Account Only**: Designed and tested exclusively with MT5 demo accounts
- **Rate Limits**: DeepSeek free tier is limited to 40 requests per minute
- **Not Financial Advice**: This is a research and simulation platform, not a production trading system

## License

MIT

## Disclaimer

This software is for **educational and research purposes only**. It is designed to operate on **demo accounts** and should not be used for live trading with real money. The authors are not responsible for any financial losses. Past performance of any trading system is not indicative of future results.
