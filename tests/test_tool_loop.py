"""The research agents' optional tool-call loop, against the fake terminal."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest

from agents.tool_loop import run_tool_loop
from tests.fakes.mt5 import FakeMT5


class Script:
    def __init__(self, *outputs: dict[str, Any] | str) -> None:
        self.outputs = deque(o if isinstance(o, str) else json.dumps(o) for o in outputs)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.outputs.popleft()


ENABLED = {"enabled": True, "max_iterations": 2, "max_tools": 3, "max_result_chars": 200}
FINAL = {"action": "HOLD", "confidence": 0.4}


@pytest.fixture
def mt5(fake_mt5: FakeMT5) -> FakeMT5:
    fake_mt5.add_symbol("EURUSD")
    return fake_mt5


def test_disabled_loop_is_a_single_completion(mt5, mt5_cfg) -> None:
    complete = Script(FINAL)
    _, parsed = run_tool_loop(complete, "analyse", {"enabled": False}, mt5_cfg)
    assert parsed == FINAL
    assert complete.prompts == ["analyse"]


def test_tool_result_is_fed_back(mt5, mt5_cfg) -> None:
    complete = Script(
        {"tool_request": {"name": "get_symbol_info", "args": {"symbol": "EURUSD"}}}, FINAL
    )
    _, parsed = run_tool_loop(complete, "analyse", ENABLED, mt5_cfg)

    assert parsed == FINAL
    assert "Available tools" in complete.prompts[0]
    assert "Tool results" in complete.prompts[1]
    assert '"tool": "get_symbol_info"' in complete.prompts[1]
    assert mt5.initialized is False


def test_long_tool_results_are_truncated(mt5, mt5_cfg) -> None:
    complete = Script({"tool": "get_symbols", "args": {}}, FINAL)
    for i in range(30):
        mt5.add_symbol(f"SYM{i:02d}")
    run_tool_loop(complete, "analyse", ENABLED, mt5_cfg)
    assert "...<truncated>" in complete.prompts[1]


def test_tool_errors_are_reported_to_the_model(mt5, mt5_cfg) -> None:
    complete = Script({"tool_request": {"name": "get_symbol_info", "args": {"bogus": 1}}}, FINAL)
    run_tool_loop(complete, "analyse", ENABLED, mt5_cfg)
    assert '"error"' in complete.prompts[1]


def test_iterations_are_bounded(mt5, mt5_cfg) -> None:
    ask = {"tool_request": {"name": "get_account_info", "args": {}}}
    complete = Script(ask, ask, ask, ask)
    _, parsed = run_tool_loop(complete, "analyse", ENABLED, mt5_cfg)
    assert len(complete.prompts) == ENABLED["max_iterations"] + 1
    assert "tool_request" in parsed  # the caller's schema validation rejects it


def test_missing_terminal_path(mt5) -> None:
    complete = Script({"tool_request": {"name": "get_account_info", "args": {}}}, FINAL)
    run_tool_loop(complete, "analyse", ENABLED, {})
    assert "missing_terminal_path" in complete.prompts[1]
