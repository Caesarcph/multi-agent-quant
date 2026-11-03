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
    # Assertions compare plain text; CI sets FORCE_COLOR for readable logs.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")


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


def test_config_show_json_is_machine_readable() -> None:
    import json

    result = runner.invoke(app, ["config", "show", "tool", "--json", "-c", str(REPO_CONFIG)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["mt5"]["default_symbol"] == "EURUSD"
    assert data["correlation"]["symbols"] == ["EURUSD", "GBPUSD", "USDJPY"]


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


# --------------------------------------------------------------------------- kill switch
def test_kill_switch_cycle(tmp_path: Path) -> None:
    state_file = tmp_path / "kill.json"
    args = ["--file", str(state_file)]

    status = runner.invoke(app, ["kill-switch", "status", *args])
    assert status.exit_code == 0
    assert "Not engaged" in status.output

    engaged = runner.invoke(
        app, ["kill-switch", "engage", "--mode", "flatten", "-r", "CPI surprise", *args]
    )
    assert engaged.exit_code == 0, engaged.output
    assert "Kill switch engaged (flatten)" in engaged.output

    status = runner.invoke(app, ["kill-switch", "status", *args])
    assert status.exit_code == 3
    assert "CPI surprise" in status.output

    from maq_risk.kill_switch import KillSwitch
    from maq_risk.models import KillSwitchMode

    state = KillSwitch(state_file).state()
    assert state is not None and state.mode is KillSwitchMode.FLATTEN

    assert "released" in runner.invoke(app, ["kill-switch", "release", *args]).output
    assert "was not engaged" in runner.invoke(app, ["kill-switch", "release", *args]).output


def test_kill_switch_uses_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAQ_KILL_SWITCH_FILE", str(tmp_path / "env-kill.json"))
    result = runner.invoke(app, ["kill-switch", "engage", "-r", "test"])
    assert result.exit_code == 0
    assert (tmp_path / "env-kill.json").exists()


# --------------------------------------------------------------------------- audit
def _event_log(path: Path) -> None:
    from datetime import UTC, datetime

    from maq_engine import DayStarted, EventStore

    with EventStore(path) as store:
        for day in range(1, 4):
            store.append(
                DayStarted(
                    ts=datetime(2026, 11, day, tzinfo=UTC),
                    day=datetime(2026, 11, day).date(),
                    balance=10_000,
                    equity=10_000,
                )
            )


def test_audit_verify_and_head(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _event_log(db)
    verified = runner.invoke(app, ["audit", "verify", "--db", str(db)])
    assert verified.exit_code == 0, verified.output
    assert "3 record(s) intact" in verified.output

    head = runner.invoke(app, ["audit", "head", "--db", str(db)])
    seq, digest = head.output.split()
    assert seq == "3" and len(digest) == 64
    anchored = runner.invoke(app, ["audit", "verify", "--db", str(db), "--anchor", digest])
    assert anchored.exit_code == 0
    wrong = runner.invoke(app, ["audit", "verify", "--db", str(db), "--anchor", "0" * 64])
    assert wrong.exit_code == 1
    assert "anchor" in wrong.output


def test_audit_detects_tampering(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "events.db"
    _event_log(db)
    raw = sqlite3.connect(db, isolation_level=None)
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET payload = replace(payload, '10000', '99999') WHERE seq = 2")
    raw.close()
    result = runner.invoke(app, ["audit", "verify", "--db", str(db)])
    assert result.exit_code == 1
    assert "chain broken at record 2" in result.output


def test_audit_missing_database(tmp_path: Path) -> None:
    for command in (["audit", "verify"], ["audit", "head"]):
        result = runner.invoke(app, [*command, "--db", str(tmp_path / "nope.db")])
        assert result.exit_code == 2
        assert "does not exist" in result.output


def test_doctor_warns_while_the_kill_switch_is_engaged(tmp_path: Path) -> None:
    from maq_cli.doctor import check_kill_switch
    from maq_risk.kill_switch import KillSwitch

    state_file = tmp_path / "kill.json"
    environ = {"MAQ_KILL_SWITCH_FILE": str(state_file)}
    [ok] = run_checks(REPO_CONFIG, environ=environ, checks=[check_kill_switch])
    assert ok.status is Status.OK

    KillSwitch(state_file).engage(reason="maintenance")
    [warn] = run_checks(REPO_CONFIG, environ=environ, checks=[check_kill_switch])
    assert warn.status is Status.WARN
    assert "maintenance" in warn.summary
