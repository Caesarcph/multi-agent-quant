"""DeepSeek daily analyst for end-of-day summaries."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from deepseek.client import DeepSeekClient
from deepseek.utils import parse_json_output


def build_daily_prompt(
    summary: Dict[str, Any],
    account_state: Optional[Dict[str, Any]],
    daily_state: Optional[Dict[str, Any]],
) -> str:
    payload = {
        "summary": summary,
        "account_state": account_state,
        "daily_state": daily_state,
    }
    return (
        "You are a senior trading analyst. Generate an end-of-day report.\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "performance_summary": "...",\n'
        '  "model_behavior": "...",\n'
        '  "risk_observations": "...",\n'
        '  "recommendations": "..."\n'
        "}\n\n"
        f"Data:\n{json.dumps(payload, indent=2)}\n"
    )


class DeepSeekDailyAnalyst:
    def __init__(self, client: DeepSeekClient) -> None:
        self._client = client
        self.last_raw_output: Optional[str] = None

    def generate_daily_report(
        self,
        summary: Dict[str, Any],
        account_state: Optional[Dict[str, Any]],
        daily_state: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = build_daily_prompt(summary, account_state, daily_state)
        raw_output = self._client.analyze(prompt, task="daily_summary")
        self.last_raw_output = raw_output
        parsed, error = parse_json_output(raw_output)
        return {
            "prompt": prompt,
            "raw_output": raw_output,
            "parsed": parsed,
            "error": error,
        }
