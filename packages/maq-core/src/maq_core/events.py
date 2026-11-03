"""Domain events: the append-only record the platform can be rebuilt from.

Every decision, risk verdict, order and fill becomes an ``Event``. A subclass
declares a unique ``kind`` (``kind: Literal["order.submitted"] = "order.submitted"``)
and is registered automatically, so ``parse_event`` can turn stored JSON back
into the right class. Classes without a ``kind`` default are abstract groupings
and are not registered.

``canonical_json`` is the stable text form that is stored and hashed by the
audit chain. Hashes are computed over the stored text, never over a
re-serialisation, so adding an optional field later does not break old chains.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from maq_core.domain import UtcDatetime

_EVENT_TYPES: dict[str, type[Event]] = {}


def new_event_id() -> str:
    """A UUIDv7 (time-ordered) as 32 hex characters."""
    millis = time.time_ns() // 1_000_000
    value = (millis & ((1 << 48) - 1)) << 80 | int.from_bytes(os.urandom(10), "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return uuid.UUID(int=value).hex


class UnknownEventError(ValueError):
    """Stored data names an event kind that no loaded module defines."""


class Event(BaseModel):
    """Base class of every domain event."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: str
    ts: UtcDatetime
    event_id: str = Field(default_factory=new_event_id)
    cycle_id: str | None = None
    # The event that led to this one, e.g. the risk decision behind an order.
    causation_id: str | None = None

    # Set on registered subclasses; handy for isinstance-free dispatch.
    KIND: ClassVar[str | None] = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        default = cls.model_fields["kind"].default
        if not isinstance(default, str) or not default:
            return  # abstract grouping such as BrokerEvent
        existing = _EVENT_TYPES.get(default)
        if existing is not None and _qualified_name(existing) != _qualified_name(cls):
            raise TypeError(
                f"event kind {default!r} is defined twice: "
                f"{_qualified_name(existing)} and {_qualified_name(cls)}"
            )
        _EVENT_TYPES[default] = cls
        cls.KIND = default


def _qualified_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def event_types() -> Mapping[str, type[Event]]:
    """Every registered event class by kind (read-only view)."""
    return MappingProxyType(_EVENT_TYPES)


def parse_event(data: str | bytes | Mapping[str, Any]) -> Event:
    """Rebuild an event from its JSON text or a decoded mapping."""
    obj: Any = json.loads(data) if isinstance(data, str | bytes) else dict(data)
    if not isinstance(obj, dict):
        raise ValueError("an event must be a JSON object")
    kind = obj.get("kind")
    cls = _EVENT_TYPES.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise UnknownEventError(f"unknown event kind {kind!r}; is the defining module imported?")
    return cls.model_validate(obj)


def canonical_json(event: Event) -> str:
    """Compact JSON with sorted keys: the form that is stored and hashed."""
    return json.dumps(
        event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
