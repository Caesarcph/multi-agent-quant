"""External data plugin interface and loader."""
from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import import_module
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ExternalDataPlugin(ABC):
    """Base interface for external data plugins."""

    name: str = ""

    @abstractmethod
    def get_economic_calendar(self, days_ahead: int = 7) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_news(self, lookback_days: int = 3) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_sentiment(self, symbol: str | None = None) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_macro_series(self, series: list[str] | None = None) -> list[dict]:
        raise NotImplementedError


def _plugin_name(plugin: ExternalDataPlugin) -> str:
    name = getattr(plugin, "name", "") or plugin.__class__.__name__
    return str(name)


def load_plugins(plugin_specs: Iterable[str]) -> Tuple[List[ExternalDataPlugin], List[Dict[str, str]]]:
    plugins: List[ExternalDataPlugin] = []
    errors: List[Dict[str, str]] = []
    for spec in plugin_specs:
        spec = str(spec).strip()
        if not spec:
            continue
        try:
            module_path, class_name = spec.split(":", 1)
            module = import_module(module_path)
            cls = getattr(module, class_name)
            plugin = cls()
            if not isinstance(plugin, ExternalDataPlugin):
                raise TypeError("plugin_class_not_external_data_plugin")
            plugins.append(plugin)
        except Exception as exc:  # noqa: BLE001
            errors.append({"plugin": spec, "error": str(exc)})
    return plugins, errors


def _extend_list(target: List[Any], payload: Any) -> None:
    if payload is None:
        return
    if isinstance(payload, list):
        target.extend(payload)
        return
    target.append(payload)


def _limit_items(values: List[Any], max_items: Optional[int]) -> List[Any]:
    if max_items is None:
        return values
    return values[: int(max_items)]


def collect_external_data(config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    plugin_specs = config.get("plugins", []) or []
    plugins, errors = load_plugins(plugin_specs)

    include = config.get("include") or [
        "economic_calendar",
        "news",
        "sentiment",
        "macro",
    ]
    include_set = {str(item) for item in include}
    days_ahead = int(config.get("days_ahead", 7))
    lookback_days = int(config.get("lookback_days", 3))
    max_items = config.get("max_items")
    symbol = config.get("symbol")
    macro_series = config.get("macro_series")

    economic_calendar: List[Any] = []
    news: List[Any] = []
    sentiment: List[Any] = []
    macro: List[Any] = []

    for plugin in plugins:
        name = _plugin_name(plugin)
        try:
            if "economic_calendar" in include_set:
                data = plugin.get_economic_calendar(days_ahead=days_ahead)
                _extend_list(economic_calendar, data)
            if "news" in include_set:
                data = plugin.get_news(lookback_days=lookback_days)
                _extend_list(news, data)
            if "sentiment" in include_set:
                data = plugin.get_sentiment(symbol=symbol)
                _extend_list(sentiment, data)
            if "macro" in include_set:
                data = plugin.get_macro_series(series=macro_series)
                _extend_list(macro, data)
        except Exception as exc:  # noqa: BLE001
            errors.append({"plugin": name, "error": str(exc)})

    payload: Dict[str, Any] = {}
    if "economic_calendar" in include_set:
        payload["economic_calendar"] = _limit_items(economic_calendar, max_items)
    if "news" in include_set:
        payload["news"] = _limit_items(news, max_items)
    if "sentiment" in include_set:
        payload["sentiment"] = _limit_items(sentiment, max_items)
    if "macro" in include_set:
        payload["macro"] = _limit_items(macro, max_items)

    return payload, errors
