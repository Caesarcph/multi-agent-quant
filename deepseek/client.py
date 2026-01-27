"""DeepSeek client wrapper with optional rate limiting."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from openai import OpenAI

from data.agent_audit import log_agent_output
from deepseek.rate_limiter import DeepSeekRateLimiter
from tools.config_loader import load_yaml


class DeepSeekClient:
    def __init__(self, config: Dict[str, Any], agent_name: str = "deepseek_aux") -> None:
        self._config = config
        self._agent_name = agent_name
        api_cfg = config.get("api", {})
        self._model = api_cfg.get("model")
        self._base_url = api_cfg.get("base_url")
        api_key_env = api_cfg.get("api_key_env")
        self._api_key = os.environ.get(api_key_env or "")
        if not self._api_key:
            raise RuntimeError(f"Missing API key env: {api_key_env}")
        self._defaults = config.get("request_defaults", {})

        rate_cfg = config.get("rate_limit", {}) or {}
        rpm = int(rate_cfg.get("requests_per_minute", 0))
        safety_margin = float(rate_cfg.get("safety_margin", 1.0))
        self._limiter = None
        if rpm > 0:
            self._limiter = DeepSeekRateLimiter(rpm_limit=rpm, safety_margin=safety_margin)

        self._logging_cfg = config.get("logging", {}) or {}
        self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)

        self.last_prompt: Optional[str] = None
        self.last_raw_output: Optional[str] = None
        self.last_reasoning: Optional[str] = None

    @property
    def model(self) -> str:
        return str(self._model)

    def analyze(
        self,
        prompt: str,
        system: Optional[str] = None,
        task: str = "aux",
        **overrides: Any,
    ) -> str:
        self.last_prompt = prompt
        content, reasoning = self._request(prompt, system, overrides)
        self.last_raw_output = content
        self.last_reasoning = reasoning
        self._log_output(task=task, prompt=prompt, raw_output=content, reasoning=reasoning)
        return content

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        task: str = "aux",
        **overrides: Any,
    ) -> str:
        """Send a pre-built chat message list and return the assistant content."""
        prompt = json.dumps(messages, ensure_ascii=True)
        self.last_prompt = prompt
        content, reasoning = self._request_messages(messages, overrides)
        self.last_raw_output = content
        self.last_reasoning = reasoning
        self._log_output(task=task, prompt=prompt, raw_output=content, reasoning=reasoning)
        return content

    def _request(
        self,
        prompt: str,
        system: Optional[str],
        overrides: Dict[str, Any],
    ) -> Tuple[str, Optional[str]]:
        if self._limiter:
            self._limiter.wait_if_needed()

        defaults = dict(self._defaults)
        defaults.update(overrides)

        temperature = defaults.get("temperature")
        top_p = defaults.get("top_p")
        max_tokens = defaults.get("max_tokens")
        stream = bool(defaults.get("stream", False))
        thinking = bool(defaults.get("thinking", False))

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return self._request_messages(
            messages,
            {
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "stream": stream,
                "thinking": thinking,
            },
        )

    def _request_messages(
        self,
        messages: list[dict[str, str]],
        overrides: Dict[str, Any],
    ) -> Tuple[str, Optional[str]]:
        if self._limiter:
            self._limiter.wait_if_needed()

        defaults = dict(self._defaults)
        defaults.update(overrides)

        temperature = defaults.get("temperature")
        top_p = defaults.get("top_p")
        max_tokens = defaults.get("max_tokens")
        stream = bool(defaults.get("stream", False))
        thinking = bool(defaults.get("thinking", False))

        extra_body = None
        if thinking:
            extra_body = {"chat_template_kwargs": {"thinking": True}}

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body=extra_body,
            stream=stream,
        )

        if self._limiter:
            self._limiter.record()

        if not stream:
            content = response.choices[0].message.content or ""
            return content, None

        content = ""
        reasoning_chunks = ""
        for chunk in response:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_chunks += reasoning
            if delta.content:
                content += delta.content
        return content, reasoning_chunks or None

    def _log_output(
        self,
        task: str,
        prompt: str,
        raw_output: str,
        reasoning: Optional[str],
    ) -> None:
        if not self._logging_cfg.get("enabled", False):
            return
        directory = self._logging_cfg.get("directory", "logs/deepseek")
        max_chars = int(self._logging_cfg.get("max_chars", 4000))
        output_dir = Path(directory)
        log_agent_output(
            output_dir=output_dir,
            agent_name=f"{self._agent_name}_{task}",
            model=self.model,
            prompt=prompt,
            raw_output=raw_output,
            reasoning=reasoning,
            max_chars=max_chars,
        )


def load_deepseek_config(config_dir: Path) -> Dict[str, Any]:
    return load_yaml(config_dir / "deepseek_config.yaml")
