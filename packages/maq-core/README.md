# maq-core

Shared foundations for the multi-agent-quant platform:

- `maq_core.domain` — immutable domain types: `SymbolSpec` (pips, pip value, notional,
  margin, volume grid), `Tick`, `Bar`, `AccountState`, `Position`, `OrderRequest` (with an
  idempotency key), `OrderResult`, `WorkingOrder`, `ClosedTrade`. Timestamps are always
  timezone-aware UTC; NaN and infinity are rejected.
- `maq_core.events` — the `Event` base class for the append-only event log, with a kind
  registry (`parse_event`) and the canonical JSON form used by the audit hash chain.
- `maq_core.clock` — `SystemClock` for live code, `ManualClock` for replay and tests.
- `maq_core.config` — typed schema for the files in `config/`, environment overrides,
  hard risk limits, and checks that block unsafe settings for live trading.
- `maq_core.logging` — structured logging (structlog) for both new and legacy code.

See `docs/roadmap/` in the repository root for where this is heading.
