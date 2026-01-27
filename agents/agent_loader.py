"""Agent factory from YAML config."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List

from agents.base_agent import AgentConfig, BaseAgent
from agents.cli_agent import CLICompletionAgent
from agents.deepseek_agent import DeepSeekAgent
from agents.prompt_loader import load_output_schema, load_system_prompt
from tools.config_loader import load_yaml


def _ensure_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def load_agents(config_dir: Path) -> list[BaseAgent]:
    system_prompt = load_system_prompt()
    schema = load_output_schema()

    tool_cfg = load_yaml(config_dir / "tool_config.yaml")
    tool_calls_cfg = tool_cfg.get("tool_calls", {})
    mt5_cfg = tool_cfg.get("mt5", {})

    model_cfg = load_yaml(config_dir / "model_config.yaml")
    cli_cfg = model_cfg.get("cli", {})
    cli_defaults = cli_cfg.get("defaults", {})

    agents: list[BaseAgent] = []
    for name in ("gpt", "claude", "gemini"):
        cfg = cli_cfg.get(name, {})
        if not cfg.get("enabled", False):
            continue
        command = cfg.get("command")
        if not command:
            raise RuntimeError(f"Missing CLI command for {name} in model_config.yaml")
        args = _ensure_list(cfg.get("args", cli_defaults.get("args")))
        timeout = cfg.get("timeout_seconds", cli_defaults.get("timeout_seconds", 120))
        response_json_field = cfg.get("response_json_field", cli_defaults.get("response_json_field"))
        model_name = cfg.get("model", name)
        agent_cfg = AgentConfig(name=name, model=model_name)
        agents.append(
            CLICompletionAgent(
                config=agent_cfg,
                schema=schema,
                system_prompt=system_prompt,
                command=command,
                args=args,
                timeout_seconds=timeout,
                response_json_field=response_json_field,
                tool_config={"tool_calls": tool_calls_cfg},
                mt5_config=mt5_cfg,
            )
        )

    deepseek_cfg = load_yaml(config_dir / "deepseek_config.yaml")
    if deepseek_cfg.get("enabled", False):
        api_cfg = deepseek_cfg.get("api", {})
        defaults = deepseek_cfg.get("request_defaults", {})
        repair_cfg = deepseek_cfg.get("repair", {})
        agent_cfg = AgentConfig(name="deepseek", model=api_cfg.get("model", "deepseek"))
        agents.append(
            DeepSeekAgent(
                config=agent_cfg,
                schema=schema,
                system_prompt=system_prompt,
                base_url=api_cfg.get("base_url", ""),
                api_key_env=api_cfg.get("api_key_env", ""),
                temperature=defaults.get("temperature", 1.0),
                top_p=defaults.get("top_p", 0.95),
                max_tokens=defaults.get("max_tokens", 8192),
                thinking=defaults.get("thinking", True),
                stream=defaults.get("stream", True),
                repair_enabled=repair_cfg.get("enabled", True),
                repair_max_chars=repair_cfg.get("max_chars", 8000),
                repair_temperature=repair_cfg.get("temperature", 0.0),
                repair_top_p=repair_cfg.get("top_p", 1.0),
                repair_max_tokens=repair_cfg.get("max_tokens", 2048),
                repair_thinking=repair_cfg.get("thinking", False),
                repair_stream=repair_cfg.get("stream", False),
                tool_config={"tool_calls": tool_calls_cfg},
                mt5_config=mt5_cfg,
            )
        )

    return agents
