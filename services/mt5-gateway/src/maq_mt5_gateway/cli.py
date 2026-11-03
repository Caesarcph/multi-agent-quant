"""``maq-gateway``: run the gateway next to a MetaTrader 5 terminal (Windows)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from datetime import timedelta

TOKEN_ENV = "MAQ_GATEWAY_TOKEN"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="maq-gateway", description=__doc__)
    parser.add_argument("--terminal", help="path to terminal64.exe (default: auto-detect)")
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--login", type=int, help="account number (default: the logged-in one)")
    parser.add_argument("--server", help="trade server name, with --login")
    parser.add_argument(
        "--server-offset-hours",
        type=float,
        default=0.0,
        help="trade server time minus UTC, e.g. 2 or 3 for most FX brokers",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0, help="seconds between polls")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get(TOKEN_ENV, "")
    if len(token) < 16:
        print(f"Set {TOKEN_ENV} to a random string of at least 16 characters.", file=sys.stderr)
        return 2

    import uvicorn

    from maq_brokers.base import BrokerConnectionError
    from maq_brokers.mt5 import Mt5Broker
    from maq_mt5_gateway.app import create_app

    broker = Mt5Broker(
        terminal_path=args.terminal,
        login=args.login,
        password=os.environ.get("MAQ_MT5_PASSWORD"),
        server=args.server,
        server_time_offset=timedelta(hours=args.server_offset_hours),
    )

    async def serve() -> None:
        await broker.connect()
        app = create_app(broker, token=token, poll=broker.poll, poll_interval=args.poll_interval)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port))
        try:
            await server.serve()
        finally:
            await broker.close()

    try:
        asyncio.run(serve())
    except BrokerConnectionError as error:
        print(f"Cannot connect to MetaTrader 5: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
