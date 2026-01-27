"""Trade history loader."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import MetaTrader5 as mt5


def get_deals_today() -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    deals = mt5.history_deals_get(start, now)
    if deals is None:
        return []
    return [deal._asdict() for deal in deals]
