"""Trading Department Agent - Executes signals with MT5 tool access."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from deepseek.client import DeepSeekClient
from tools import mt5_connection
from tools.tool_registry import call_tool, list_tools

logger = logging.getLogger(__name__)

TRADING_SYSTEM_PROMPT = """You are the Head of Trading at a quantitative hedge fund.
Your job is to receive trading signals from the Research Department and execute them properly.

You have DIRECT ACCESS to the MT5 trading platform via tools. Use them to:
1. Check symbol specifications (min lot, lot step, tick value, margin requirements)
2. Check account status (balance, equity, free margin)
3. Check current positions and orders
4. Calculate proper position sizes

WORKFLOW:
1. Receive a signal from Research (direction, symbol, confidence)
2. Call tools to gather information you need
3. Decide: EXECUTE, MODIFY, or REJECT the signal
4. If EXECUTE: provide exact order parameters

AVAILABLE TOOLS (call one at a time):
{tools}

To call a tool, respond with:
{{"tool_request": {{"name": "tool_name", "args": {{...}}}}}}

You may call at most {max_tool_calls} tools before making a final decision.

When ready to execute, respond with:
{{
  "decision": "EXECUTE" | "REJECT" | "MODIFY",
  "reason": "brief explanation",
  "rationale": {{
    "basis": "technical" | "fundamental" | "mixed",
    "time_horizon": "scalp" | "intra" | "swing" | "position",
    "sl_tp_rationale": "why SL/TP are placed where they are",
    "sizing_rationale": "why this lot size fits margin/risk",
    "risk_notes": "key risks or invalidation conditions"
  }},
  "order": {{
    "symbol": "EURUSD",
    "action": "BUY" | "SELL",
    "volume": 0.01,
    "sl_price": 1.0800,
    "tp_price": 1.0950,
    "comment": "research_signal_id"
  }}
}}

If REJECT, set "order" to null.

RULES:
- NEVER guess lot sizes. Always check symbol_info for volume_min and volume_step.
- NEVER exceed free margin. Check account_info first.
- Round volume to symbol's volume_step.
- Calculate SL/TP as actual prices, not pips.
- Be conservative. When in doubt, REJECT.
"""


class TradingAgent:
    """Trading department agent with MT5 tool access."""

    def __init__(
        self,
        client: DeepSeekClient,
        mt5_cfg: Dict[str, Any],
        max_iterations: int = 6,
        max_tool_calls: int = 4,
        max_tool_result_chars: int = 4000,
    ):
        self.client = client
        self.mt5_cfg = mt5_cfg
        self.max_iterations = max_iterations
        self.max_tool_calls = max(1, min(max_tool_calls, max_iterations - 1))
        self.max_tool_result_chars = max(500, int(max_tool_result_chars))
        self.last_tool_calls: List[Dict[str, Any]] = []

    def process_signal(
        self,
        research_signal: Dict[str, Any],
        account_info: Optional[Dict[str, Any]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Process a research signal and return execution decision.

        Args:
            research_signal: Signal from research department (fused or individual)
            account_info: Optional pre-fetched account info
            positions: Optional pre-fetched positions

        Returns:
            Decision dict with 'decision', 'reason', 'order' fields
        """
        self.last_tool_calls = []

        # Build initial context
        context = {
            "research_signal": research_signal,
            "pre_loaded": {
                "account_info": account_info,
                "positions": positions,
            },
        }

        tools = list_tools()
        system_prompt = TRADING_SYSTEM_PROMPT.format(
            tools=json.dumps(tools, indent=2),
            max_tool_calls=self.max_tool_calls,
        )

        user_prompt = f"""Research Department has sent you this signal:

{json.dumps(research_signal, indent=2)}

Pre-loaded context (may be None):
- Account info: {json.dumps(account_info, indent=2) if account_info else 'Not provided, call get_account_info'}
- Positions: {json.dumps(positions, indent=2) if positions else 'Not provided, call get_positions'}

Analyze this signal and decide whether to EXECUTE, MODIFY, or REJECT.
Use tools as needed to gather missing information."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Run tool loop
        tool_calls = 0
        decision_requested = False
        for _ in range(self.max_iterations):
            if tool_calls >= self.max_tool_calls and not decision_requested:
                messages.append({
                    "role": "user",
                    "content": (
                        "Tool limit reached. Provide your FINAL decision now. "
                        "Do NOT request more tools."
                    ),
                })
                decision_requested = True

            response = self.client.complete_chat(messages)
            parsed = self._safe_parse(response)

            if parsed is None:
                logger.warning("Trading agent returned unparseable response")
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "Invalid JSON. Please respond with valid JSON only.",
                })
                continue

            # Check if tool request
            tool_request = self._extract_tool_request(parsed)
            if tool_request:
                if tool_calls >= self.max_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "Tool limit reached. Provide your FINAL decision now.",
                    })
                    continue
                tool_result = self._call_tool(tool_request)
                self.last_tool_calls.append({
                    "tool": tool_request.get("name"),
                    "args": tool_request.get("args", {}),
                    "result": tool_result,
                })
                tool_calls += 1
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"Tool result:\n{json.dumps(tool_result, indent=2)}\n\nContinue analysis or provide final decision.",
                })
                continue

            # Check if final decision
            if "decision" in parsed:
                return self._validate_decision(parsed, research_signal)

            # Unknown response
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": "Please provide either a tool_request or your final decision.",
            })

        # Max iterations reached
        return {
            "decision": "REJECT",
            "reason": "Max iterations reached without decision",
            "order": None,
            "tool_calls": self.last_tool_calls,
        }

    def _call_tool(self, tool_request: Dict[str, Any]) -> Any:
        """Execute a tool call with MT5 connection."""
        tool_name = tool_request.get("name")
        tool_args = tool_request.get("args", {})

        if not tool_name:
            return {"error": "missing_tool_name"}

        terminal_path = self.mt5_cfg.get("terminal_path")
        if not terminal_path:
            return {"error": "missing_terminal_path"}

        mt5_connection.initialize(
            terminal_path=terminal_path,
            timeout_seconds=self.mt5_cfg.get("initialize_timeout_seconds", 30),
        )
        try:
            result = call_tool(tool_name, **tool_args)
            logger.info("Trading agent called %s -> %s", tool_name, type(result).__name__)
            return self._trim_tool_result(tool_name, result)
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            return {"error": str(exc)}
        finally:
            mt5_connection.shutdown()

    def _trim_tool_result(self, tool_name: str, result: Any) -> Any:
        """Limit tool payload size to keep prompts within model limits."""
        if tool_name == "get_symbols" and isinstance(result, list):
            names = [item.get("name") for item in result if isinstance(item, dict)]
            sample = names[:50]
            return {
                "symbols": sample,
                "total": len(result),
                "truncated": len(result) > len(sample),
            }

        if isinstance(result, list) and len(result) > 50:
            return {
                "items": result[:50],
                "total": len(result),
                "truncated": True,
            }

        try:
            payload = json.dumps(result, ensure_ascii=True)
        except TypeError:
            return result

        if len(payload) <= self.max_tool_result_chars:
            return result

        return {
            "truncated": True,
            "total_chars": len(payload),
            "sample": payload[: self.max_tool_result_chars],
        }

    def _safe_parse(self, output: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from response."""
        try:
            # Try direct parse
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON object
        try:
            start = output.find("{")
            end = output.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(output[start:end])
        except json.JSONDecodeError:
            pass

        return None

    def _extract_tool_request(self, parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract tool request from parsed response."""
        if "tool_request" in parsed and isinstance(parsed["tool_request"], dict):
            return parsed["tool_request"]
        return None

    def _validate_decision(
        self,
        decision: Dict[str, Any],
        original_signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validate and enrich the decision."""
        result = {
            "decision": decision.get("decision", "REJECT"),
            "reason": decision.get("reason", "No reason provided"),
            "rationale": decision.get("rationale"),
            "order": decision.get("order"),
            "tool_calls": self.last_tool_calls,
            "original_signal": original_signal,
        }

        # Validate order if EXECUTE
        if result["decision"] == "EXECUTE" and result["order"]:
            order = result["order"]
            required = ["symbol", "action", "volume"]
            missing = [f for f in required if f not in order]
            if missing:
                result["decision"] = "REJECT"
                result["reason"] = f"Order missing required fields: {missing}"
                result["order"] = None

        return result


def create_trading_agent(
    config_dir,
    mt5_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[TradingAgent]:
    """Factory to create trading agent from config."""
    from pathlib import Path
    from deepseek.client import load_deepseek_config

    config_dir = Path(config_dir)
    deepseek_cfg = load_deepseek_config(config_dir)

    if not deepseek_cfg or not deepseek_cfg.get("enabled", False):
        logger.warning("DeepSeek not enabled, cannot create trading agent")
        return None

    if mt5_cfg is None:
        from tools.config_loader import load_yaml
        tool_cfg = load_yaml(config_dir / "tool_config.yaml")
        mt5_cfg = tool_cfg.get("mt5", {})

    client = DeepSeekClient(deepseek_cfg, agent_name="trading_dept")
    return TradingAgent(client, mt5_cfg)
