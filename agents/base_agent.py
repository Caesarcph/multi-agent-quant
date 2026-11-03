"""Agent base classes and shared configuration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentConfig:
    name: str
    model: str
    enabled: bool = True
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class BaseAgent(ABC):
    def __init__(self, config: AgentConfig, schema: dict[str, Any], system_prompt: str) -> None:
        self.name = config.name
        self.model = config.model
        self.config = config
        self.schema = schema
        self.system_prompt = system_prompt

    @abstractmethod
    def generate_signal(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Return a validated signal dict."""
        raise NotImplementedError
