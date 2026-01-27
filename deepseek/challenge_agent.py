"""DeepSeek challenge agent for split decisions."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from deepseek.client import DeepSeekClient
from deepseek.utils import parse_json_output


def _build_signal_summary(signals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        model = signal.get("model")
        if not model:
            continue
        summary[str(model)] = {
            "action": signal.get("action"),
            "confidence": signal.get("confidence"),
            "horizon": signal.get("horizon"),
            "reason_codes": signal.get("reason_codes", []),
        }
    return summary


def _snapshot_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    rates = snapshot.get("rates", []) or []
    return {
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "timestamp": snapshot.get("timestamp"),
        "account": snapshot.get("account"),
        "positions": snapshot.get("positions"),
        "recent_rates": rates[-20:],
    }


def build_challenge_prompt(
    fusion_result: Dict[str, Any],
    signals: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> str:
    summary = _build_signal_summary(signals)
    snapshot_compact = _snapshot_summary(snapshot)
    payload = {
        "fusion": fusion_result,
        "signals": summary,
        "snapshot": snapshot_compact,
    }
    return (
        "You are a senior risk advisor reviewing a split trading decision.\n"
        "Evaluate which side is stronger, the risks of each action, and whether to proceed.\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "recommendation": "PROCEED|REDUCE_SIZE|ABSTAIN",\n'
        '  "favored_action": "BUY|SELL|HOLD",\n'
        '  "risk_assessment": "LOW|MEDIUM|HIGH",\n'
        '  "reasoning": "brief explanation"\n'
        "}\n\n"
        f"Context:\n{json.dumps(payload, indent=2)}\n"
    )


def apply_challenge_result(
    signal: Dict[str, Any],
    challenge: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if not config.get("apply_result", False):
        return signal

    updated = dict(signal)
    recommendation = str(challenge.get("recommendation", "")).upper()
    favored_action = str(challenge.get("favored_action", "")).upper()

    if recommendation == "ABSTAIN":
        updated["action"] = "HOLD"
        return updated

    if recommendation == "REDUCE_SIZE":
        reduction_factor = float(config.get("reduction_factor", 0.5))
        updated["volume"] = float(updated.get("volume", 0.0)) * reduction_factor
        risk = updated.get("risk")
        if isinstance(risk, dict):
            risk_fraction = float(risk.get("risk_fraction_of_initial", 0.0))
            risk["risk_fraction_of_initial"] = risk_fraction * reduction_factor
        return updated

    if recommendation == "PROCEED" and config.get("allow_override", False):
        if favored_action in {"BUY", "SELL", "HOLD"}:
            updated["action"] = favored_action

    return updated


class DeepSeekChallengeAgent:
    def __init__(self, client: DeepSeekClient, config: Dict[str, Any]) -> None:
        self._client = client
        self._config = config
        self.last_raw_output: Optional[str] = None

    def challenge_decision(
        self,
        fusion_result: Dict[str, Any],
        signals: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = build_challenge_prompt(fusion_result, signals, snapshot)
        raw_output = self._client.analyze(prompt, task="challenge")
        self.last_raw_output = raw_output
        parsed, error = parse_json_output(raw_output)
        return {
            "prompt": prompt,
            "raw_output": raw_output,
            "parsed": parsed,
            "error": error,
        }
