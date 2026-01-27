"""DeepSeek agent wrapper using the OpenAI client."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from openai import OpenAI

from agents.base_agent import AgentConfig, BaseAgent
from agents.json_utils import extract_json_object
from agents.prompt_builder import build_prompt
from agents.schema_validator import validate_signal
from agents.tool_loop import run_tool_loop


class DeepSeekAgent(BaseAgent):
    def __init__(
        self,
        config: AgentConfig,
        schema: Dict[str, Any],
        system_prompt: str,
        base_url: str,
        api_key_env: str,
        temperature: float = 1.0,
        top_p: float = 0.95,
        max_tokens: int = 8192,
        thinking: bool = True,
        stream: bool = True,
        repair_enabled: bool = True,
        repair_max_chars: int = 8000,
        repair_temperature: float = 0.0,
        repair_top_p: float = 1.0,
        repair_max_tokens: int = 2048,
        repair_thinking: bool = False,
        repair_stream: bool = False,
        tool_config: Dict[str, Any] | None = None,
        mt5_config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config, schema, system_prompt)
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {api_key_env}")
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.stream = stream
        self.repair_enabled = repair_enabled
        self.repair_max_chars = repair_max_chars
        self.repair_temperature = repair_temperature
        self.repair_top_p = repair_top_p
        self.repair_max_tokens = repair_max_tokens
        self.repair_thinking = repair_thinking
        self.repair_stream = repair_stream
        self.last_reasoning = ""
        self.last_raw_response = ""
        self.last_prompt = ""
        self.last_repair_output = ""
        self.tool_config = tool_config or {}
        self.mt5_config = mt5_config or {}

    def _collect_stream(self, response: Any) -> str:
        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        for chunk in response:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_chunks.append(reasoning)
            if delta.content is not None:
                content_chunks.append(delta.content)
        self.last_reasoning = "".join(reasoning_chunks)
        return "".join(content_chunks).strip()

    def _request_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        thinking: bool,
        stream: bool,
    ) -> Any:
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"thinking": thinking}},
            stream=stream,
        )

    def _repair_output(self, raw_output: str) -> str:
        schema_json = json.dumps(self.schema, indent=2, sort_keys=True)
        trimmed = raw_output[: self.repair_max_chars]
        repair_prompt = (
            "You are a JSON repair tool. Output ONLY a single valid JSON object that "
            "matches the schema. Do not add commentary, markdown, or code fences. "
            "Ensure numeric fields satisfy schema minimums; if stop_loss_pips or "
            "take_profit_pips are negative, make them positive."
        )
        user_message = f"Schema:\\n{schema_json}\\n\\nInvalid response:\\n{trimmed}"
        messages = [
            {"role": "system", "content": repair_prompt},
            {"role": "user", "content": user_message},
        ]
        response = self._request_completion(
            messages=messages,
            temperature=self.repair_temperature,
            top_p=self.repair_top_p,
            max_tokens=self.repair_max_tokens,
            thinking=self.repair_thinking,
            stream=self.repair_stream,
        )
        if self.repair_stream:
            return self._collect_stream(response)
        return (response.choices[0].message.content or "").strip()

    def generate_signal(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        prompt = build_prompt(snapshot, self.schema)
        tool_cfg = self.tool_config.get("tool_calls", {})

        def complete(user_prompt: str) -> str:
            self.last_prompt = f"{self.system_prompt}\n\n{user_prompt}"
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = self._request_completion(
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                thinking=self.thinking,
                stream=self.stream,
            )
            output = (
                self._collect_stream(response)
                if self.stream
                else response.choices[0].message.content
            )
            if output is None:
                raise RuntimeError("DeepSeek response contained no content.")
            return output

        output, parsed = run_tool_loop(
            complete=complete,
            base_prompt=prompt,
            tool_cfg=tool_cfg,
            mt5_cfg=self.mt5_config,
        )
        self.last_raw_response = output
        try:
            signal = parsed if parsed is not None else extract_json_object(output)
            validate_signal(signal, self.schema)
            signal["model"] = self.name
            return signal
        except Exception:
            if not self.repair_enabled:
                raise
            repaired = self._repair_output(output)
            self.last_repair_output = repaired
            signal = extract_json_object(repaired)
            validate_signal(signal, self.schema)
            signal["model"] = self.name
            return signal
