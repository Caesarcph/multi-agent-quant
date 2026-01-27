"""Optional tool-call loop for agents."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Tuple

from agents.json_utils import extract_json_object
from tools import mt5_connection
from tools.tool_registry import call_tool, list_tools


def build_tool_instructions(max_tools: int = 12) -> str:
    tools = list_tools()[:max_tools]
    return (
        "If you need more data, respond with JSON:\n"
        "{\n"
        '  "tool_request": {\n'
        '    "name": "get_rates",\n'
        '    "args": { "symbol": "EURUSD", "timeframe": "M1", "count": 50 }\n'
        "  }\n"
        "}\n"
        "Available tools:\n"
        f"{json.dumps(tools, indent=2)}\n"
        "Otherwise return the final trading signal JSON.\n"
    )


def run_tool_loop(
    complete: Callable[[str], str],
    base_prompt: str,
    tool_cfg: Dict[str, Any],
    mt5_cfg: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    enabled = bool(tool_cfg.get("enabled", False))
    if not enabled:
        output = complete(base_prompt)
        return output, _safe_parse(output)

    max_iterations = int(tool_cfg.get("max_iterations", 2))
    include_tools = bool(tool_cfg.get("include_tools_in_prompt", True))
    max_tools = int(tool_cfg.get("max_tools", 12))
    max_result_chars = int(tool_cfg.get("max_result_chars", 4000))

    prompt = base_prompt
    if include_tools:
        prompt = f"{prompt}\n\n{build_tool_instructions(max_tools=max_tools)}"

    tool_history: list[Dict[str, Any]] = []
    output = ""
    parsed: Optional[Dict[str, Any]] = None

    for _ in range(max_iterations + 1):
        output = complete(prompt)
        parsed = _safe_parse(output)
        tool_request = _extract_tool_request(parsed)
        if tool_request is None:
            return output, parsed

        tool_name = tool_request.get("name") or tool_request.get("tool")
        tool_args = tool_request.get("args", {})
        tool_result = _call_tool(tool_name, tool_args, mt5_cfg)
        tool_history.append(
            {
                "tool": tool_name,
                "args": tool_args,
                "result": _truncate_json(tool_result, max_result_chars),
            }
        )
        prompt = (
            f"{base_prompt}\n\nTool results:\n{json.dumps(tool_history, indent=2)}\n\n"
            "Now provide the final trading signal JSON only."
        )

    return output, parsed


def _safe_parse(output: str) -> Optional[Dict[str, Any]]:
    try:
        data = extract_json_object(output)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _extract_tool_request(parsed: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(parsed, dict):
        return None
    if "tool_request" in parsed and isinstance(parsed["tool_request"], dict):
        return parsed["tool_request"]
    if "tool" in parsed:
        return {"name": parsed.get("tool"), "args": parsed.get("args", {})}
    return None


def _call_tool(tool_name: str, tool_args: Dict[str, Any], mt5_cfg: Dict[str, Any]) -> Any:
    if not tool_name:
        return {"error": "missing_tool_name"}
    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        return {"error": "missing_terminal_path"}
    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        return call_tool(tool_name, **tool_args)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    finally:
        mt5_connection.shutdown()


def _truncate_json(payload: Any, max_chars: int) -> Any:
    text = json.dumps(payload)
    if len(text) <= max_chars:
        return payload
    return text[:max_chars] + "...<truncated>"
