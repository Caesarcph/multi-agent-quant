# Operations Guide

This runbook covers setup, configuration, and day-to-day commands for the MT5 demo system.

## Requirements

- Windows with MetaTrader 5 installed (terminal path configured).
- Python 3.10+ (current environment uses 3.14).
- Demo account already logged in (MT5 terminal running).

## Environment Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

Set DeepSeek credentials (NVIDIA endpoint):

```powershell
setx NVIDIA_API_KEY "<your-key>"
```

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

- Set `config/execution_config.yaml`:
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
- Reports are written to `reports/daily`.
