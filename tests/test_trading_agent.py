"""TradingAgent: the tool loop against the fake terminal, and what may reach execution."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest

from agents.trading_agent import TradingAgent
from tests.fakes.mt5 import FakeMT5


class ScriptedLLM:
    """Stands in for DeepSeekClient.complete_chat: replies are played back in order."""

    def __init__(self, *replies: dict[str, Any] | str) -> None:
        self.replies = deque(r if isinstance(r, str) else json.dumps(r) for r in replies)
        self.calls: list[list[dict[str, str]]] = []

    def complete_chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append([dict(m) for m in messages])
        return self.replies.popleft() if self.replies else json.dumps({"thinking": "..."})


RESEARCH = {"symbol": "EURUSD", "action": "BUY", "confidence": 0.7}


def order(**overrides: Any) -> dict[str, Any]:
    payload = {
        "symbol": "EURUSD",
        "action": "BUY",
        "volume": 0.1,
        "sl_price": 1.08,
        "tp_price": 1.09,
    }
    payload.update(overrides)
    return payload


def execute(**overrides: Any) -> dict[str, Any]:
    return {"decision": "EXECUTE", "reason": "fits margin", "order": order(**overrides)}


@pytest.fixture
def agent_factory(fake_mt5: FakeMT5, mt5_cfg: dict[str, Any]):
    fake_mt5.add_symbol("EURUSD")

    def make(*replies: dict[str, Any] | str, **kwargs: Any) -> tuple[TradingAgent, ScriptedLLM]:
        llm = ScriptedLLM(*replies)
        return TradingAgent(llm, mt5_cfg, **kwargs), llm

    return make


def test_tool_call_then_execute(agent_factory, fake_mt5: FakeMT5) -> None:
    agent, llm = agent_factory(
        {"tool_request": {"name": "get_account_info", "args": {}}},
        execute(),
    )
    decision = agent.process_signal(RESEARCH, account_info=None, positions=None)

    assert decision["decision"] == "EXECUTE"
    assert decision["order"]["volume"] == 0.1
    (call,) = decision["tool_calls"]
    assert call["tool"] == "get_account_info"
    assert call["result"]["balance"] == 10000.0
    # The tool result went back to the model, and the terminal was released.
    assert "Tool result" in llm.calls[1][-1]["content"]
    assert fake_mt5.initialized is False


def test_prefetched_context_is_in_the_prompt(agent_factory) -> None:
    agent, llm = agent_factory(execute())
    agent.process_signal(RESEARCH, account_info={"balance": 4321.0}, positions=[])
    assert '"balance": 4321.0' in llm.calls[0][1]["content"]


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (execute(symbol="GBPUSD"), "Order symbol GBPUSD differs from research symbol EURUSD"),
        (execute(action="SELL"), "Order action SELL differs from research action BUY"),
        (execute(volume=0), "Order volume must be positive"),
        (execute(volume="lots"), "Order volume must be positive"),
        (
            '{"decision": "EXECUTE", "order": {"symbol": "EURUSD", "action": "BUY", "volume": NaN}}',
            "Order volume must be positive",
        ),
        ({"decision": "MODIFY", "order": {"symbol": "EURUSD"}}, "Order missing required fields"),
        ({"decision": "EXECUTE", "order": None}, "EXECUTE without an order"),
        ({"decision": "BUY_NOW", "order": order()}, "Unknown decision: 'BUY_NOW'"),
    ],
)
def test_orders_that_deviate_from_research_are_rejected(agent_factory, reply, reason) -> None:
    agent, _ = agent_factory(reply)
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert decision["decision"] == "REJECT"
    assert decision["order"] is None
    assert reason in decision["reason"]


def test_modify_and_lowercase_decision_are_accepted(agent_factory) -> None:
    agent, _ = agent_factory({"decision": "modify", "order": order(volume=0.05)})
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert decision["decision"] == "MODIFY"
    assert decision["order"]["volume"] == 0.05


def test_reject_never_carries_an_order(agent_factory) -> None:
    agent, _ = agent_factory({"decision": "REJECT", "reason": "spread", "order": order()})
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert decision["decision"] == "REJECT"
    assert decision["order"] is None


def test_tool_budget_is_enforced(agent_factory) -> None:
    ask = {"tool_request": {"name": "get_account_info", "args": {}}}
    agent, llm = agent_factory(ask, ask, ask, ask, execute(), max_iterations=6, max_tool_calls=2)
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])

    assert decision["decision"] == "EXECUTE"
    assert len(decision["tool_calls"]) == 2
    assert any("Tool limit reached" in m["content"] for m in llm.calls[-1] if m["role"] == "user")


def test_invalid_json_gets_one_more_chance(agent_factory) -> None:
    agent, llm = agent_factory("I think we should buy.", execute())
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert decision["decision"] == "EXECUTE"
    assert "Invalid JSON" in llm.calls[1][-1]["content"]


def test_no_decision_within_budget_is_a_reject(agent_factory) -> None:
    agent, _ = agent_factory(max_iterations=3)
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert decision["decision"] == "REJECT"
    assert decision["reason"] == "Max iterations reached without decision"


def test_failing_tool_returns_an_error_to_the_model(agent_factory) -> None:
    agent, _ = agent_factory(
        {"tool_request": {"name": "get_symbol_info", "args": {"symbol": "XXXYYY"}}},
        execute(),
    )
    decision = agent.process_signal(RESEARCH, account_info={}, positions=[])
    assert "error" in decision["tool_calls"][0]["result"]
    assert decision["decision"] == "EXECUTE"
