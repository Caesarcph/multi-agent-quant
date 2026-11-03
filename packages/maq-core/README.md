# maq-core

Shared foundations for the multi-agent-quant platform:

- `maq_core.config` — typed schema for the files in `config/`, environment overrides,
  hard risk limits, and checks that block unsafe settings for live trading.
- `maq_core.clock` — a clock abstraction so the same code runs live and in replay.
- `maq_core.logging` — structured logging (structlog) for both new and legacy code.

See `docs/roadmap/` in the repository root for where this is heading.
