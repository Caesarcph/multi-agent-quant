# Repository Guidelines

Multi-agent LLM trading system: four research agents (GPT/Codex, Claude, Gemini, DeepSeek) analyse an MT5 market snapshot, their signals are fused by weighted voting, a DeepSeek trading agent turns the result into an order, and risk guards decide whether it reaches MetaTrader 5. `CLAUDE.md` has the full architecture; the 2026 upgrade plan is in `docs/roadmap/`.

## Project Structure

- `scripts/run_loop.py` – main entry point (scan → research → fusion → risk → trading dept → execution).
- `orchestrator/` – single-cycle runner used by `scripts/run_cycle.py`.
- `agents/` – CLI and DeepSeek agent wrappers, trading agent, prompts and output schema.
- `fusion/` – weighted voting with regime agreement; `decision_signal()` is what downstream code acts on.
- `risk/` – trade/daily/account/exposure/correlation guards and position sizing.
- `execution/` – MT5 order execution and safety checks.
- `data/`, `tools/`, `deepseek/`, `validation/`, `plugins/` – state, MT5 helpers, auxiliary DeepSeek tasks, paper trading/backtests, external data.
- `config/*.yaml` – all runtime configuration. `tests/` – pytest suite.

## Commands (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\pytest                                 # tests
.\.venv\Scripts\ruff check .                           # lint
.\.venv\Scripts\python scripts\run_loop.py --max-cycles 1   # one dry-run cycle
.\.venv\Scripts\python scripts\run_loop.py --live           # send orders to the demo account
```

MT5 integration tests only run with `MT5_INTEGRATION=1` and a running terminal.

## Safety Rules

- `scripts/run_loop.py` never sends orders without `--live`. Do not reintroduce a hard-coded `dry_run = False`.
- Keep `enforcement.enabled: true` in `config/risk_config.yaml`; turning it off skips the drawdown hard stop and all daily/exposure limits.
- Lot size comes from the risk budget (`use_risk_sizing: true`). An LLM-proposed volume may only shrink it (`risk/position_sizer.py::size_order`); do not switch on `use_signal_volume`.
- Act on `decision_signal(fused)`, never on `fused["representative_signal"]` directly: the representative can be BUY/SELL when the ensemble decided HOLD.
- Keep API keys in environment variables (`NVIDIA_API_KEY`); never commit `.env`, `logs/` or `reports/`.

## Coding Style

- Python 3.10+, 4-space indentation, `snake_case` functions and variables, `CapWords` classes.
- YAML/JSON config keys use `lower_snake_case`.
- Tests live in `tests/test_*.py`, mirroring module names; add a test for every bug fix.

## Commits and Pull Requests

- Concise, imperative commit messages (e.g. "Add MT5 tool registry").
- PRs state the problem, the approach, and how it was tested.
