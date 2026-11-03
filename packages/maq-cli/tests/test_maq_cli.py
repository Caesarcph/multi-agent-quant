"""`maq` command line and doctor checks."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from maq_cli.app import app
from maq_cli.doctor import Status, run_checks

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"
runner = CliRunner()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("MAQ_"):
            monkeypatch.delenv(name)


def by_name(results):
    return {result.name: result for result in results}


# --------------------------------------------------------------------------- commands
def test_config_validate_ok() -> None:
    result = runner.invoke(app, ["config", "validate", "-c", str(REPO_CONFIG)])
    assert result.exit_code == 0, result.output
    assert "is valid" in result.output


def test_config_validate_reports_every_problem(config_dir: Path) -> None:
    path = config_dir / "risk_config.yaml"
    data = yaml.safe_load(path.read_text())
    data["trade_level"]["per_trade_risk"] = 0.05
    data["enforcment"] = {"enabled": True}
    path.write_text(yaml.safe_dump(data))

    result = runner.invoke(app, ["config", "validate", "-c", str(config_dir)])

    assert result.exit_code == 1
    assert "2 problem(s)" in result.output
    assert "trade_level.per_trade_risk" in result.output
    assert "enforcment: Extra inputs are not permitted" in result.output


def test_config_validate_warns_about_live_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAQ_EXECUTION__USE_SIGNAL_VOLUME", "true")
    result = runner.invoke(app, ["config", "validate", "-c", str(REPO_CONFIG)])
    assert result.exit_code == 0
    assert "use_signal_volume is true" in result.output


def test_config_show_applies_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK", "0.005")
    result = runner.invoke(app, ["config", "show", "risk", "-c", str(REPO_CONFIG)])
    assert result.exit_code == 0, result.output
    shown = yaml.safe_load(result.output)
    assert shown["trade_level"]["per_trade_risk"] == 0.005
    assert shown["enforcement"]["enabled"] is True


def test_config_show_unknown_section() -> None:
    result = runner.invoke(app, ["config", "show", "riskk", "-c", str(REPO_CONFIG)])
    assert result.exit_code == 2
    assert "Unknown section 'riskk'" in result.output


def test_doctor_exit_code_reflects_failures(config_dir: Path, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    result = runner.invoke(app, ["doctor", "-c", str(config_dir)])
    assert result.exit_code == 1  # the API key check fails
    assert "NVIDIA_API_KEY is not set" in result.output


# --------------------------------------------------------------------------- doctor checks
def test_doctor_on_a_ready_machine(config_dir: Path, tmp_path: Path) -> None:
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"")
    environ = {"NVIDIA_API_KEY": "x", "MAQ_TOOL__MT5__TERMINAL_PATH": str(terminal)}

    results = by_name(
        run_checks(
            config_dir,
            environ=environ,
            platform="linux",  # MetaTrader5 cannot be imported here
            which=lambda command: f"/usr/bin/{command}",
        )
    )

    assert results["Config"].status is Status.OK
    assert results["Live safety"].status is Status.OK
    assert results["MT5 terminal"].status is Status.OK
    assert results["DeepSeek API key"].status is Status.OK
    assert results["CLI agents"].status is Status.OK
    assert results["Log directory"].status is Status.OK
    assert results["MetaTrader5"].status is Status.WARN


def test_doctor_flags_missing_pieces(config_dir: Path) -> None:
    results = by_name(
        run_checks(config_dir, environ={}, platform="win32", which=lambda command: None)
    )
    assert results["DeepSeek API key"].status is Status.FAIL
    assert results["MT5 terminal"].status is Status.FAIL  # a missing terminal is fatal on Windows
    assert results["CLI agents"].status is Status.WARN
    assert "gpt (codex)" in results["CLI agents"].summary


def test_doctor_reports_invalid_config_and_skips_dependents(config_dir: Path) -> None:
    (config_dir / "execution_config.yaml").write_text("dry_runn: true\n")
    results = by_name(run_checks(config_dir, environ={}))
    assert results["Config"].status is Status.FAIL
    assert "execution_config.yaml: dry_runn" in results["Config"].details[0]
    assert results["Live safety"].summary == "skipped: config is invalid"


def test_doctor_unsafe_live_settings(config_dir: Path) -> None:
    results = by_name(run_checks(config_dir, environ={"MAQ_RISK__ENFORCEMENT__ENABLED": "false"}))
    assert results["Live safety"].status is Status.WARN
    assert "--live" in results["Live safety"].summary


def test_a_crashing_check_does_not_hide_the_others(config_dir: Path) -> None:
    def broken(ctx):
        raise RuntimeError("bug")

    results = run_checks(config_dir, environ={}, checks=[broken])
    assert results[0].status is Status.FAIL
    assert "check crashed: bug" in results[0].summary
