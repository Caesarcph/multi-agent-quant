"""Local JSON plugin for external data."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.external_plugins import ExternalDataPlugin


class LocalJsonPlugin(ExternalDataPlugin):
    name = "local_json"

    def __init__(self) -> None:
        self._path = self._resolve_path()
        self._payload = self._load_payload()

    def get_economic_calendar(self, days_ahead: int = 7) -> list[dict]:
        return list(self._payload.get("economic_calendar", []))

    def get_news(self, lookback_days: int = 3) -> list[dict]:
        return list(self._payload.get("news", []))

    def get_sentiment(self, symbol: str | None = None) -> list[dict]:
        items = list(self._payload.get("sentiment", []))
        if symbol:
            return [item for item in items if str(item.get("symbol", "")).upper() == symbol]
        return items

    def get_macro_series(self, series: list[str] | None = None) -> list[dict]:
        items = list(self._payload.get("macro", []))
        if series:
            allow = {str(name) for name in series}
            return [item for item in items if str(item.get("series")) in allow]
        return items

    def _resolve_path(self) -> Optional[Path]:
        env_path = os.environ.get("EXTERNAL_DATA_PATH")
        if env_path:
            return Path(env_path)
        default_path = Path("config") / "external_data.json"
        if default_path.exists():
            return default_path
        return None

    def _load_payload(self) -> Dict[str, Any]:
        if not self._path or not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
