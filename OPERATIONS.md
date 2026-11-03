# Operations Guide

This runbook covers setup, configuration, and day-to-day commands for the MT5 demo system.

## Requirements

- Windows with MetaTrader 5 installed (terminal path configured).
- Python 3.11+ (3.13 recommended) and [uv](https://docs.astral.sh/uv/).
- Demo account already logged in (MT5 terminal running).

## Environment Setup

```powershell
uv sync                 # or: pip install -r requirements.txt -r requirements-dev.txt
uv run maq doctor       # checks everything below and tells you what is missing
```

Set DeepSeek credentials (NVIDIA endpoint):

```powershell
setx NVIDIA_API_KEY "<your-key>"
```

Machine-specific settings go into environment variables instead of the tracked YAML files.
Any key works: `MAQ_<FILE>__<KEY>__<SUBKEY>`, for example:

```powershell
setx MAQ_TOOL__MT5__TERMINAL_PATH "D:/AVAmt5/terminal64.exe"
setx MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK "0.005"
```

`uv run maq config show risk` prints the effective values. Config errors stop `run_loop.py` at
start-up with every problem listed; `--live` is refused while risk guards, risk-based sizing or
safety checks are switched off. `MAQ_CONFIG_STRICT=0` temporarily downgrades validation errors to
warnings.

## Key Configuration Files

- MT5 path and snapshot defaults: `config/tool_config.yaml`
- Agent CLI commands: `config/model_config.yaml`
- DeepSeek endpoint and params: `config/deepseek_config.yaml`
- Fusion thresholds: `config/fusion_config.yaml`
- Risk limits: `config/risk_config.yaml`
- Execution toggles: `config/execution_config.yaml`

## Common Commands

Smoke test MT5 connectivity:

```powershell
.\.venv\Scripts\python scripts\mt5_smoke.py --count 5
```

Run a single cycle (dry-run execution):

```powershell
.\.venv\Scripts\python scripts\run_cycle.py --agents deepseek
```

Enable live demo orders:

- Main loop (`scripts/run_loop.py`): pass `--live`. Without it every order is a dry run, regardless of the config.
- Single-cycle scripts (`scripts/run_cycle.py`): set `config/execution_config.yaml`:
  - `enabled: true`
  - `dry_run: false`

Then run:

```powershell
.\.venv\Scripts\python scripts\run_cycle.py --agents deepseek
```

Generate daily report:

```powershell
.\.venv\Scripts\python scripts\generate_report.py
```

## Operational Toggles

- Agent output audit: `config/tool_config.yaml` -> `audit.enabled`
- Correlation guard: `config/tool_config.yaml` -> `correlation.enabled`
- Daily state persistence: `config/tool_config.yaml` -> `daily_state.enabled`
- Max spread/liq filters: `config/execution_config.yaml`

## Safety Notes

- This is demo trading only. Keep `dry_run: true` until you verify outputs.
- Start with a single agent (`--agents deepseek`) to simplify debugging.
- Cycle outputs are written to `logs/cycles`.
- Logs: `logs/orchestrator.log` (readable) and `logs/orchestrator.jsonl` (one JSON object per line,
  with `cycle` and `symbol` on every line). Both rotate at 10 MB, keeping 5 files.
- Reports are written to `reports/daily`.
