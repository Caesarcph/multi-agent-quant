"""DeepSeek research agent and client, end to end through the real openai SDK.

Requests go to tests.fakes.llm_server.FakeLLMServer over HTTP, so request
building, JSON parsing and SSE streaming are all exercised without network access.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agents.base_agent import AgentConfig
from agents.deepseek_agent import DeepSeekAgent
from agents.prompt_loader import load_output_schema
from deepseek.client import DeepSeekClient
from tests.factories import research_signal
from tests.fakes.llm_server import FakeLLMServer

SNAPSHOT = {"symbol": "EURUSD", "timeframe": "M15", "bars": [{"close": 1.085}]}


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLLMServer]:
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "test-key")
    with FakeLLMServer(model="deepseek-test") as fake:
        yield fake


def make_agent(server: FakeLLMServer, **kwargs: Any) -> DeepSeekAgent:
    return DeepSeekAgent(
        config=AgentConfig(name="deepseek", model="deepseek-test"),
        schema=load_output_schema(),
        system_prompt="You are an analyst.",
        base_url=server.base_url,
        api_key_env="TEST_DEEPSEEK_KEY",
        **kwargs,
    )


# --------------------------------------------------------------------------- agent
@pytest.mark.parametrize("stream", [False, True])
def test_generate_signal(server: FakeLLMServer, stream: bool) -> None:
    server.reply(json.dumps(research_signal(model="gpt")), reasoning="looking at the trend")
    agent = make_agent(server, stream=stream, thinking=True)

    signal = agent.generate_signal(SNAPSHOT)

    assert signal["model"] == "deepseek"
    assert signal["action"] == "BUY"
    (request,) = server.requests
    assert request["model"] == "deepseek-test"
    assert request["stream"] is stream
    assert request["chat_template_kwargs"] == {"thinking": True}
    assert request["messages"][0] == {"role": "system", "content": "You are an analyst."}
    if stream:
        assert agent.last_reasoning == "looking at the trend"


def test_markdown_wrapped_json_is_accepted(server: FakeLLMServer) -> None:
    server.reply("Here you go:\n```json\n" + json.dumps(research_signal()) + "\n```")
    assert make_agent(server, stream=False).generate_signal(SNAPSHOT)["action"] == "BUY"


def test_invalid_output_is_repaired(server: FakeLLMServer) -> None:
    server.reply("BUY EURUSD with high confidence")  # no JSON at all
    server.reply(json.dumps(research_signal(action="SELL")))
    agent = make_agent(server, stream=False)

    signal = agent.generate_signal(SNAPSHOT)

    assert signal["action"] == "SELL"
    repair = server.requests[1]
    assert "JSON repair tool" in repair["messages"][0]["content"]
    assert repair["temperature"] == 0.0
    assert "BUY EURUSD with high confidence" in repair["messages"][1]["content"]


def test_schema_violation_without_repair_raises(server: FakeLLMServer) -> None:
    server.reply(json.dumps(research_signal(confidence=3.0)))
    with pytest.raises(ValueError):
        make_agent(server, stream=False, repair_enabled=False).generate_signal(SNAPSHOT)
    assert server.pending == 0


def test_missing_api_key_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TEST_DEEPSEEK_KEY"):
        DeepSeekAgent(
            config=AgentConfig(name="deepseek", model="m"),
            schema={},
            system_prompt="",
            base_url="http://127.0.0.1:9/v1",
            api_key_env="TEST_DEEPSEEK_KEY",
        )


# --------------------------------------------------------------------------- client
def client_config(server: FakeLLMServer, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "api": {
            "base_url": server.base_url,
            "model": "deepseek-test",
            "api_key_env": "TEST_DEEPSEEK_KEY",
        },
        "request_defaults": {"temperature": 0.2, "max_tokens": 64, "stream": False},
    }
    config.update(overrides)
    return config


def test_client_plain_and_streamed(server: FakeLLMServer) -> None:
    client = DeepSeekClient(client_config(server))

    server.reply("plain answer")
    assert client.analyze("question", system="sys") == "plain answer"

    server.reply("streamed answer", reasoning="step by step")
    assert client.analyze("question", stream=True) == "streamed answer"
    assert client.last_reasoning == "step by step"

    first, second = server.requests
    assert first["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
    ]
    assert first["temperature"] == 0.2
    assert second["stream"] is True


def test_client_chat_with_thinking(server: FakeLLMServer) -> None:
    client = DeepSeekClient(client_config(server))
    server.reply('{"decision": "REJECT"}')
    messages = [{"role": "user", "content": "decide"}]
    assert client.complete_chat(messages, thinking=True) == '{"decision": "REJECT"}'
    assert server.requests[0]["chat_template_kwargs"] == {"thinking": True}


def test_client_writes_audit_log_when_enabled(server: FakeLLMServer, tmp_path: Path) -> None:
    logging_cfg = {"enabled": True, "directory": str(tmp_path / "deepseek"), "max_chars": 100}
    client = DeepSeekClient(client_config(server, logging=logging_cfg), agent_name="aux")
    server.reply("summary text")
    client.analyze("prompt", task="daily")
    (log_file,) = (tmp_path / "deepseek").rglob("*.json")
    payload = json.loads(log_file.read_text(encoding="utf-8"))
    assert payload["raw_output"] == "summary text"


def test_client_surfaces_http_errors(server: FakeLLMServer) -> None:
    client = DeepSeekClient(client_config(server))
    server.fail(400, "bad request")
    with pytest.raises(Exception, match="bad request"):
        client.analyze("question")
