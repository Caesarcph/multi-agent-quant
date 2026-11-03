# maq-mt5-gateway

A thin HTTP service that owns one MetaTrader 5 terminal, so everything else in
multi-agent-quant can run on Linux, macOS or in a container
([ADR-0004](../../docs/adr/0004-mt5-deployment.md)).

```text
platform (any OS) ── Mt5GatewayBroker ── HTTPS + bearer token ──> maq-gateway (Windows) ── MetaTrader5 ── terminal
```

- One worker thread talks to the terminal (`maq_brokers.mt5.Mt5Broker`); every route maps onto
  the `Broker` interface, so the client side is just another broker.
- Every route but `/health` needs `Authorization: Bearer <token>`; the token must be at least
  16 characters.
- Orders keep their client order id end to end: a retried `POST /orders` never trades twice.
- Fills, server-side stops and closes are polled from the deal history and served to clients
  by long polling (`GET /events?after=<seq>&wait=<seconds>`).

## Run it (on the Windows machine with the terminal)

```bash
setx MAQ_GATEWAY_TOKEN "<a long random string>"
uv run maq-gateway --terminal "C:/Program Files/MetaTrader 5/terminal64.exe" --host 127.0.0.1 --port 8765
```

Bind to `127.0.0.1` and reach it through an SSH tunnel or a VPN; if it must listen on a public
address, put it behind a TLS reverse proxy. Then, on the platform side:

```python
from maq_brokers.gateway import Mt5GatewayBroker

broker = Mt5GatewayBroker("http://127.0.0.1:8765", token=os.environ["MAQ_GATEWAY_TOKEN"])
await broker.connect()
```

Without Windows, the gateway is tested in-process against a simulated trade server
(`tests/fakes/mt5_server.py`) with the shared broker contract suite.
