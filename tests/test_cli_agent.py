"""CLICompletionAgent runs a real subprocess (`python -c ...`) standing in for codex/claude/gemini."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from agents.base_agent import AgentConfig
from agents.cli_agent import CLICompletionAgent
from agents.prompt_loader import load_output_schema
from tests.factories import research_signal

# Echo the prompt's length into the file given as argv[1], or exit with a code.
WRITE_OUTPUT_FILE = "import sys; open(sys.argv[1], 'w').write(sys.stdin.read()[:5])"
FAIL = "import sys; sys.stderr.write('boom'); sys.exit(3)"
SLEEP = "import time; time.sleep(5)"


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route tempfile into an isolated directory so leaked files are visible."""
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def make_agent(args: list[str], **kwargs) -> CLICompletionAgent:
    return CLICompletionAgent(
        config=AgentConfig(name="claude", model="cli"),
        schema=load_output_schema(),
        system_prompt="system",
        command="python",
        args=args,
        timeout_seconds=kwargs.pop("timeout_seconds", 30),
        **kwargs,
    )


def test_reads_output_file_and_removes_it(temp_dir: Path) -> None:
    agent = make_agent(["-c", WRITE_OUTPUT_FILE, "{output_file}"])
    assert agent._run_cli("hello world") == "hello"
    assert list(temp_dir.iterdir()) == []


def test_failed_command_raises_and_cleans_up(temp_dir: Path) -> None:
    # Regression: the temp output file used to leak whenever the CLI failed.
    agent = make_agent(["-c", FAIL, "{output_file}"])
    with pytest.raises(RuntimeError, match=r"CLI command failed \(3\): boom"):
        agent._run_cli("prompt")
    assert list(temp_dir.iterdir()) == []


def test_timeout_cleans_up(temp_dir: Path) -> None:
    agent = make_agent(["-c", SLEEP, "{output_file}"], timeout_seconds=1)
    with pytest.raises(subprocess.TimeoutExpired):
        agent._run_cli("prompt")
    assert list(temp_dir.iterdir()) == []


def test_response_json_field_is_extracted() -> None:
    payload = json.dumps({"response": "  inner text  ", "stats": {}})
    agent = make_agent(["-c", f"print({payload!r})"], response_json_field="response")
    assert agent._run_cli("prompt") == "inner text"


def test_generate_signal_validates_and_tags_model() -> None:
    signal = research_signal(model="gpt")  # the agent overrides `model` with its own name
    script = f"import sys; sys.stdin.read(); print({json.dumps(signal)!r})"
    agent = make_agent(["-c", script])
    result = agent.generate_signal({"symbol": "EURUSD", "timeframe": "M15", "bars": []})
    assert result["model"] == "claude"
    assert result["action"] == "BUY"
    assert "system" in agent.last_prompt


def test_generate_signal_rejects_schema_violations() -> None:
    bad = research_signal(confidence=1.7)
    agent = make_agent(["-c", f"print({json.dumps(bad)!r})"])
    with pytest.raises(ValueError):
        agent.generate_signal({"symbol": "EURUSD"})
