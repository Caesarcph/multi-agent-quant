# maq-brokers

One asynchronous `Broker` interface for every execution venue, so the engine and the
risk kernel never care whether orders go to MetaTrader 5, a local simulator or a backtest.

- `maq_brokers.base` — the `Broker` / `MarketData` protocols, broker errors and event
  subscriptions. Business refusals come back as `OrderResult(status=REJECTED)` with a
  normalised `RejectReason`; `submit` and `close_position` are idempotent on the client
  order id.
- `maq_brokers.paper` — `PaperBroker`, a local matching engine: bid/ask fills, slippage,
  commission, swap at rollover, pending orders, SL/TP, margin and stop-out. It runs on
  replayed prices or on top of any live `MarketData` source.
- `maq_brokers.testing` — the contract test suite. Every broker implementation runs the
  same tests, so they behave the same way.
