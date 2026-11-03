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
- `packages/maq-core` – domain types, events, clock, typed config schema (hard risk limits, `MAQ_*` overrides), structured logging.
- `packages/maq-brokers` – `Broker` interface, `PaperBroker`, MT5 adapter, gateway client, broker contract tests.
- `packages/maq-risk` – deterministic risk kernel, circuit breakers, kill switch, mandate.
- `packages/maq-engine` – hash-chained event log, order manager, execution pipeline.
- `packages/maq-cli` – the `maq` command (`doctor`, `config`, `kill-switch`, `audit`).
- `services/mt5-gateway` – HTTP service that owns one MT5 terminal on Windows.
- `config/*.yaml` – all runtime configuration. `tests/` – pytest suite; `tests/fakes/` has a fake MT5 terminal and a fake OpenAI-compatible server.
- `docs/roadmap/` – the upgrade plan; `docs/adr/` – architecture decisions.

## Commands

Managed with uv; development and tests work on any OS, live trading needs Windows.

```bash
uv sync                                         # install everything
uv run pytest                                   # tests (MT5 and LLM APIs are faked)
uv run ruff check . && uv run ruff format .     # lint + format
uv run mypy                                     # strict typing for packages/
uv run maq doctor                               # is this machine ready?
uv run maq config validate                      # config/*.yaml + MAQ_* overrides
uv run maq kill-switch engage -r "reason"       # stop new positions (--mode flatten closes ours)
uv run maq audit verify                         # check the event log's hash chain
uv run python scripts/run_loop.py --max-cycles 1   # one dry-run cycle
uv run python scripts/run_loop.py --live           # send orders to the demo account
```

MT5 integration tests only run with `MT5_INTEGRATION=1` and a running terminal.

## Safety Rules

- `scripts/run_loop.py` never sends orders without `--live`. Do not reintroduce a hard-coded `dry_run = False`.
- Keep `enforcement.enabled: true` in `config/risk_config.yaml`; turning it off skips the drawdown hard stop and all daily/exposure limits.
- Lot size comes from the risk budget (`use_risk_sizing: true`). An LLM-proposed volume may only shrink it (`risk/position_sizer.py::size_order`); do not switch on `use_signal_volume`.
- Act on `decision_signal(fused)`, never on `fused["representative_signal"]` directly: the representative can be BUY/SELL when the ensemble decided HOLD.
- The trading department may only execute the researched symbol and direction; keep `TradingAgent._validate_decision` strict.
- Config keys read by code must exist in the schema (`packages/maq-core/src/maq_core/config/models.py`); never raise the hard limits in `limits.py` to make a config pass.
- Keep API keys in environment variables (`NVIDIA_API_KEY`); never commit `.env`, `logs/` or `reports/`.
- Never resend an order whose `order_send` returned no answer; look it up instead (`find_order`).
- In new code, orders go through `OrderManager` (persisted before sending), sized by `RiskKernel`; the kernel's rules (stop loss required, no rounding up to the minimum lot, mandate for real money) stay strict.

## Coding Style

- Python 3.11+, Ruff (100 columns), 4-space indentation, `snake_case` functions and variables, `CapWords` classes.
- New code goes into `packages/` with strict typing; legacy top-level modules are migrated gradually.
- YAML/JSON config keys use `lower_snake_case`.
- Tests live in `tests/test_*.py`, mirroring module names; add a test for every bug fix.

## Commits and Pull Requests

- Concise, imperative commit messages (e.g. "Add MT5 tool registry").
- PRs state the problem, the approach, and how it was tested.
