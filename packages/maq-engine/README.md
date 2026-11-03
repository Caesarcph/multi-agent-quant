# maq-engine

Where decisions turn into orders, durably.

- `maq_engine.store.EventStore` — the append-only event log in SQLite (WAL). Every record
  carries the hash of the one before it, so `verify()` (and `maq audit verify`) notices a
  changed, deleted or reordered record; database triggers refuse updates and deletes.
- `maq_engine.orders.OrderManager` — the order state machine. An order is written to the log
  *before* it is sent; the client order id makes retries safe; if the connection drops mid-send
  the outcome is looked up instead of guessed; `reconcile()` compares the log with the broker
  and reports orphan positions.
- `maq_engine.pipeline.ExecutionPipeline` — proposal → risk kernel → order manager, building
  the risk context from the broker and recording every decision.
