"""Environment checks behind `maq doctor`.

Each check is a small function returning a ``CheckResult``; the CLI only renders
them. Checks never raise: a crash inside a check is reported as a failure.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from maq_core.config import AppConfig, ConfigError, live_trading_issues, load_config
from maq_risk.kill_switch import KillSwitch, default_path


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class DoctorContext:
    config_dir: Path
    environ: Mapping[str, str]
    platform: str = sys.platform
    which: Callable[[str], str | None] = shutil.which
    config: AppConfig | None = None


Check = Callable[[DoctorContext], CheckResult]


def check_python(ctx: DoctorContext) -> CheckResult:
    # The package requires 3.11+, so reaching this check means the version is fine.
    version = ".".join(str(part) for part in sys.version_info[:3])
    return CheckResult("Python", Status.OK, f"{version} ({sys.executable})")


def check_dependencies(ctx: DoctorContext) -> CheckResult:
    modules = {"numpy": "numpy", "openai": "openai", "PyYAML": "yaml", "jsonschema": "jsonschema"}
    missing, versions = [], []
    for dist, module in modules.items():
        try:
            importlib.import_module(module)
            versions.append(f"{dist} {importlib.metadata.version(dist)}")
        except Exception:
            missing.append(dist)
    if missing:
        return CheckResult(
            "Dependencies", Status.FAIL, f"missing: {', '.join(missing)}", ["run `uv sync`"]
        )
    return CheckResult("Dependencies", Status.OK, ", ".join(versions))


def check_metatrader(ctx: DoctorContext) -> CheckResult:
    if ctx.platform != "win32":
        return CheckResult(
            "MetaTrader5",
            Status.WARN,
            "not available on this OS",
            ["Live trading needs Windows (or Wine); tests use a fake terminal."],
        )
    try:
        importlib.import_module("MetaTrader5")
    except ImportError:
        return CheckResult("MetaTrader5", Status.FAIL, "package not installed", ["run `uv sync`"])
    return CheckResult("MetaTrader5", Status.OK, importlib.metadata.version("MetaTrader5"))


def check_config(ctx: DoctorContext) -> CheckResult:
    try:
        ctx.config = load_config(ctx.config_dir, environ=ctx.environ)
    except ConfigError as error:
        return CheckResult(
            "Config",
            Status.FAIL,
            f"{len(error.issues)} problem(s) in {ctx.config_dir}",
            [str(issue) for issue in error.issues],
        )
    return CheckResult("Config", Status.OK, f"{ctx.config_dir} is valid")


def check_live_safety(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("Live safety", Status.WARN, "skipped: config is invalid")
    issues = live_trading_issues(ctx.config)
    if issues:
        return CheckResult("Live safety", Status.WARN, "`--live` would be refused", issues)
    return CheckResult("Live safety", Status.OK, "risk guards and sizing are safe for --live")


def check_terminal(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("MT5 terminal", Status.WARN, "skipped: config is invalid")
    path = ctx.config.tool.mt5.terminal_path
    if not path:
        return CheckResult(
            "MT5 terminal", Status.FAIL, "tool_config.yaml: mt5.terminal_path is not set"
        )
    if Path(path).exists():
        return CheckResult("MT5 terminal", Status.OK, path)
    hint = ["Set MAQ_TOOL__MT5__TERMINAL_PATH to this machine's terminal64.exe."]
    status = Status.FAIL if ctx.platform == "win32" else Status.WARN
    return CheckResult("MT5 terminal", status, f"not found: {path}", hint)


def check_api_key(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("DeepSeek API key", Status.WARN, "skipped: config is invalid")
    deepseek = ctx.config.deepseek
    if not deepseek.enabled:
        return CheckResult("DeepSeek API key", Status.OK, "DeepSeek agent disabled")
    name = deepseek.api.api_key_env
    if ctx.environ.get(name):
        return CheckResult("DeepSeek API key", Status.OK, f"{name} is set")
    return CheckResult(
        "DeepSeek API key",
        Status.FAIL,
        f"{name} is not set",
        ["The DeepSeek research agent and the trading department need it."],
    )


def check_cli_agents(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("CLI agents", Status.WARN, "skipped: config is invalid")
    cli = ctx.config.model.cli
    agents = {"gpt": cli.gpt, "claude": cli.claude, "gemini": cli.gemini}
    enabled = {name: agent for name, agent in agents.items() if agent.enabled}
    if not enabled:
        return CheckResult("CLI agents", Status.OK, "none enabled")
    found: list[str] = []
    missing: list[str] = []
    for name, agent in enabled.items():
        command = (agent.command or "").split()[0]
        (found if ctx.which(command) else missing).append(f"{name} ({command})")
    if missing:
        return CheckResult(
            "CLI agents",
            Status.WARN,
            f"not on PATH: {', '.join(missing)}",
            ["Those analysts will be 'on vacation' every cycle."],
        )
    return CheckResult("CLI agents", Status.OK, ", ".join(found))


def check_log_dir(ctx: DoctorContext) -> CheckResult:
    directory = Path(ctx.config.tool.logging.directory if ctx.config else "logs")
    if not directory.is_absolute():
        directory = ctx.config_dir.parent / directory
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory):
            pass
    except OSError as error:
        return CheckResult("Log directory", Status.FAIL, f"{directory}: {error}")
    return CheckResult("Log directory", Status.OK, str(directory))


def check_kill_switch(ctx: DoctorContext) -> CheckResult:
    path = default_path(dict(ctx.environ))
    if not path.is_absolute():
        path = ctx.config_dir.parent / path
    state = KillSwitch(path).state()
    if state is None:
        return CheckResult("Kill switch", Status.OK, "not engaged")
    return CheckResult(
        "Kill switch",
        Status.WARN,
        f"engaged ({state.mode}) since {state.engaged_at:%Y-%m-%d %H:%M} UTC: {state.reason}",
        ["release it with `maq kill-switch release` once it is safe to trade"],
    )


CHECKS: list[Check] = [
    check_python,
    check_dependencies,
    check_metatrader,
    check_config,
    check_live_safety,
    check_terminal,
    check_api_key,
    check_cli_agents,
    check_log_dir,
    check_kill_switch,
]


def run_checks(
    config_dir: Path,
    environ: Mapping[str, str] | None = None,
    checks: list[Check] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[CheckResult]:
    ctx = DoctorContext(
        config_dir=config_dir,
        environ=os.environ if environ is None else environ,
        platform=platform or sys.platform,
        which=which or shutil.which,
    )
    results = []
    for check in checks or CHECKS:
        try:
            results.append(check(ctx))
        except Exception as error:  # a broken check must not hide the others
            results.append(CheckResult(check.__name__, Status.FAIL, f"check crashed: {error}"))
    return results
