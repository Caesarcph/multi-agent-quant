# maq-risk

The deterministic risk kernel: LLMs propose, this code decides. No model call, no I/O
in the decision path — the same proposal and context always give the same answer.

- `RiskKernel.evaluate(proposal, context)` runs every pre-trade check (kill switch,
  circuit breakers, drawdown and daily limits, stop loss, stop/target geometry, ATR band,
  reward/risk, spread, signal age, price grounding, news blackout, session, exposure,
  correlation, mandate) and sizes the position:
  `equity × per-trade risk × confidence factor × drawdown factor ÷ loss per lot at the stop`,
  then caps it by the requested volume, margin, daily volume and mandate, and floors it
  onto the broker's lot grid. A size below the minimum lot is a rejection, never a
  minimum-lot trade.
- `RiskLimits.from_config(...)` builds the limits from `config/risk_config.yaml`; values can
  only be tighter than the hard limits in `maq_core.config.limits`.
- `CircuitBreakers` pause new positions when model calls fail or the LLM budget is spent.
- `KillSwitch` is a file both the CLI and the running loop can see: `halt_new` stops new
  positions, `flatten` also closes everything.
