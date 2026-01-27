"""CLI-backed agent wrapper."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from typing import Any, Dict, List

from agents.base_agent import AgentConfig, BaseAgent
from agents.json_utils import extract_json_object
from agents.prompt_builder import build_prompt
from agents.schema_validator import validate_signal
from agents.tool_loop import run_tool_loop


class CLICompletionAgent(BaseAgent):
    def __init__(
        self,
        config: AgentConfig,
        schema: Dict[str, Any],
        system_prompt: str,
        command: str,
        args: List[str] | None = None,
        timeout_seconds: int = 60,
        response_json_field: str | None = None,
        tool_config: Dict[str, Any] | None = None,
        mt5_config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config, schema, system_prompt)
        self.command = command
        self.args = args or []
        self.timeout_seconds = timeout_seconds
        self.response_json_field = response_json_field
        self.tool_config = tool_config or {}
        self.mt5_config = mt5_config or {}
        self.last_prompt: str | None = None
        self.last_raw_response: str | None = None

    def _run_cli(self, prompt: str) -> str:
        cmd = shlex.split(self.command) + self.args
        output_file = None
        if "{output_file}" in cmd:
            handle = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
            output_file = handle.name
            handle.close()
            cmd = [output_file if arg == "{output_file}" else arg for arg in cmd]
        if os.name == "nt":
            cmdline = subprocess.list2cmdline(cmd)
            result = subprocess.run(
                cmdline,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=True,
            )
        else:
            result = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"CLI command failed ({result.returncode}): {result.stderr.strip()}"
            )
        output_text = result.stdout.strip()
        if output_file and os.path.exists(output_file):
            output_text = (
                open(output_file, "r", encoding="utf-8", errors="ignore").read().strip()
            )
            os.remove(output_file)

        if self.response_json_field:
            payload = json.loads(output_text)
            output_text = str(payload.get(self.response_json_field, "")).strip()

        return output_text

    def generate_signal(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        prompt = build_prompt(snapshot, self.schema)
        tool_cfg = self.tool_config.get("tool_calls", {})

        def complete(user_prompt: str) -> str:
            full_prompt = f"{self.system_prompt}\n\n{user_prompt}"
            self.last_prompt = full_prompt
            return self._run_cli(full_prompt)

        output, parsed = run_tool_loop(
            complete=complete,
            base_prompt=prompt,
            tool_cfg=tool_cfg,
            mt5_cfg=self.mt5_config,
        )
        self.last_raw_response = output
        signal = parsed if parsed is not None else extract_json_object(output)
        validate_signal(signal, self.schema)
        signal["model"] = self.name
        return signal
