"""`maq` command line entry point."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from maq_cli import __version__
from maq_cli.doctor import Status, run_checks
from maq_core.config import ConfigError, live_trading_issues, load_config
from maq_engine.store import DEFAULT_EVENT_DB, EventStore
from maq_risk.kill_switch import KillSwitch
from maq_risk.models import KillSwitchMode

app = typer.Typer(
    help="multi-agent-quant command line.", no_args_is_help=True, add_completion=False
)
config_app = typer.Typer(help="Validate and inspect config/*.yaml.", no_args_is_help=True)
app.add_typer(config_app, name="config")


def _console(stderr: bool = False) -> Console:
    # Created per call so colour settings (NO_COLOR, FORCE_COLOR, TTY) are read at
    # invocation time rather than frozen when the module is imported.
    return Console(stderr=stderr)


ConfigDirOption = Annotated[
    Path,
    typer.Option(
        "--config-dir",
        "-c",
        help="Directory with tool_config.yaml, risk_config.yaml, ...",
        envvar="MAQ_CONFIG_DIR",
    ),
]

_STYLE = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "bold red"}
_ICON = {Status.OK: "✓", Status.WARN: "!", Status.FAIL: "✗"}


@app.command()
def version() -> None:
    """Print the version."""
    _console().print(f"maq {__version__}")


@app.command()
def doctor(config_dir: ConfigDirOption = Path("config")) -> None:
    """Check that this machine is ready to run the system."""
    console = _console()
    results = run_checks(config_dir)
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("")
    table.add_column("Check")
    table.add_column("Result")
    for result in results:
        style = _STYLE[result.status]
        lines = [result.summary, *(f"  • {detail}" for detail in result.details)]
        table.add_row(f"[{style}]{_ICON[result.status]}[/]", result.name, f"[{style}]{lines[0]}[/]")
        for line in lines[1:]:
            table.add_row("", "", line)
    console.print(table)

    failures = sum(r.status is Status.FAIL for r in results)
    warnings = sum(r.status is Status.WARN for r in results)
    console.print(f"\n{failures} failure(s), {warnings} warning(s)")
    raise typer.Exit(code=1 if failures else 0)


@config_app.command("validate")
def config_validate(config_dir: ConfigDirOption = Path("config")) -> None:
    """Validate every config file and MAQ_* override; exit 1 on errors."""
    console, err_console = _console(), _console(stderr=True)
    try:
        config = load_config(config_dir)
    except ConfigError as error:
        err_console.print(f"[bold red]✗ {len(error.issues)} problem(s) in {config_dir}[/]")
        for issue in error.issues:
            err_console.print(f"  • {issue}")
        raise typer.Exit(code=1) from None

    console.print(f"[green]✓ {config_dir} is valid[/]")
    for warning in live_trading_issues(config):
        console.print(f"[yellow]! not allowed with --live:[/] {warning}")


@config_app.command("show")
def config_show(
    section: Annotated[
        str | None,
        typer.Argument(help="tool, model, deepseek, fusion, risk, execution or validation"),
    ] = None,
    config_dir: ConfigDirOption = Path("config"),
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of YAML")] = False,
) -> None:
    """Print the effective configuration (defaults + files + MAQ_* overrides)."""
    err_console = _console(stderr=True)
    try:
        config = load_config(config_dir)
    except ConfigError as error:
        err_console.print(str(error), markup=False, highlight=False)
        raise typer.Exit(code=1) from None

    data = config.model_dump(mode="json")
    if section is not None:
        if section not in data:
            err_console.print(f"Unknown section '{section}'. Choose from: {', '.join(data)}")
            raise typer.Exit(code=2)
        data = data[section]
    # Plain text (no Rich markup or highlighting) so the output can be piped or saved.
    if as_json:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        typer.echo(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), nl=False)


# --------------------------------------------------------------------------- kill switch
kill_app = typer.Typer(
    help="Stop trading by hand: halt new positions, or close everything.",
    no_args_is_help=True,
)
app.add_typer(kill_app, name="kill-switch")

KillFileOption = Annotated[
    Path | None,
    typer.Option(
        "--file",
        help="Kill switch state file (default: $MAQ_KILL_SWITCH_FILE or logs/kill_switch.json)",
    ),
]


def _kill_switch(path: Path | None) -> KillSwitch:
    return KillSwitch(path)


@kill_app.command("engage")
def kill_engage(
    reason: Annotated[str, typer.Option("--reason", "-r", help="Why; shown in logs and the UI")],
    mode: Annotated[
        KillSwitchMode,
        typer.Option(
            "--mode",
            "-m",
            help="halt_new: no new positions. flatten: also close all open positions.",
        ),
    ] = KillSwitchMode.HALT_NEW,
    file: KillFileOption = None,
) -> None:
    """Engage the kill switch; running loops pick it up before their next order."""
    state = _kill_switch(file).engage(mode, reason, by=f"cli:{os.environ.get('USER', '?')}")
    _console().print(
        f"[bold red]Kill switch engaged[/] ({state.mode}) at "
        f"{state.engaged_at:%Y-%m-%d %H:%M:%S} UTC: {state.reason}"
    )


@kill_app.command("release")
def kill_release(file: KillFileOption = None) -> None:
    """Release the kill switch."""
    if _kill_switch(file).release():
        _console().print("[green]Kill switch released[/]")
    else:
        _console().print("Kill switch was not engaged")


@kill_app.command("status")
def kill_status(file: KillFileOption = None) -> None:
    """Show the kill switch state; exit code 3 while it is engaged."""
    state = _kill_switch(file).state()
    if state is None:
        _console().print("[green]Not engaged[/]: trading may continue")
        return
    _console().print(
        f"[bold red]Engaged[/] ({state.mode}) by {state.engaged_by} at "
        f"{state.engaged_at:%Y-%m-%d %H:%M:%S} UTC: {state.reason}"
    )
    raise typer.Exit(code=3)


# --------------------------------------------------------------------------- audit
audit_app = typer.Typer(help="Check the event log.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")

EventDbOption = Annotated[
    Path, typer.Option("--db", help="Event log database", envvar="MAQ_EVENT_DB")
]


@audit_app.command("verify")
def audit_verify(
    db: EventDbOption = DEFAULT_EVENT_DB,
    anchor: Annotated[
        str | None,
        typer.Option(help="Hash recorded earlier with `maq audit head`; catches a cut-off tail"),
    ] = None,
) -> None:
    """Recompute the hash chain; exit 1 if any record was changed, removed or reordered."""
    if not db.exists():
        _console(stderr=True).print(f"[bold red]✗ {db} does not exist[/]", soft_wrap=True)
        raise typer.Exit(code=2)
    with EventStore(db) as store:
        report = store.verify(expected_head=anchor)
    if report.ok:
        _console().print(
            f"[green]✓ {report.checked} record(s) intact[/], head {report.head}", soft_wrap=True
        )
        return
    where = f" at record {report.first_bad_seq}" if report.first_bad_seq else ""
    _console(stderr=True).print(
        f"[bold red]✗ chain broken{where}[/] after {report.checked} good record(s): "
        f"{report.problem}",
        soft_wrap=True,
    )
    raise typer.Exit(code=1)


@audit_app.command("head")
def audit_head(db: EventDbOption = DEFAULT_EVENT_DB) -> None:
    """Print the latest sequence number and hash, to keep somewhere else as an anchor."""
    if not db.exists():
        _console(stderr=True).print(f"[bold red]✗ {db} does not exist[/]", soft_wrap=True)
        raise typer.Exit(code=2)
    with EventStore(db) as store:
        seq, digest = store.head()
    typer.echo(f"{seq} {digest}")


def main() -> None:
    # Keep Rich from wrapping tables to 80 columns in CI logs.
    os.environ.setdefault("COLUMNS", "120")
    app()
