"""Trade history loader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import MetaTrader5 as mt5


def get_deals_today() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    deals = mt5.history_deals_get(start, now)
    if deals is None:
        return []
    return [deal._asdict() for deal in deals]
