"""maq_core.config: schema, overrides, hard limits and the legacy dict contract."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from maq_core.config import (
    HARD_LIMITS,
    ConfigError,
    env_overrides,
    live_trading_issues,
    load_config,
    load_section,
)

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the shipped config directory."""
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


def edit(config_dir: Path, filename: str, change) -> None:
    path = config_dir / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def problems(error: ConfigError) -> list[str]:
    return [str(issue) for issue in error.issues]


def test_shipped_config_is_valid_and_safe_for_live() -> None:
    config = load_config(REPO_CONFIG, environ={})
    assert config.risk.enforcement.enabled is True
    assert config.risk.trade_level.per_trade_risk == 0.01
    assert config.execution.use_risk_sizing is True
    assert live_trading_issues(config) == []


def test_typo_is_reported_with_file_and_key(config_dir: Path) -> None:
    edit(config_dir, "risk_config.yaml", lambda d: d.update(enforcment={"enabled": True}))
    with pytest.raises(ConfigError) as caught:
        load_config(config_dir, environ={})
    assert problems(caught.value) == [
        "risk_config.yaml: enforcment: Extra inputs are not permitted"
    ]


@pytest.mark.parametrize(
    ("filename", "change", "expected"),
    [
        (
            "risk_config.yaml",
            lambda d: d["trade_level"].update(per_trade_risk=0.05),
            "trade_level.per_trade_risk: Input should be less than or equal to 0.02",
        ),
        (
            "risk_config.yaml",
            lambda d: d["account_level"].update(max_drawdown=0.8),
            "account_level.max_drawdown: Input should be less than or equal to 0.5",
        ),
        (
            "risk_config.yaml",
            lambda d: d["account_level"]["drawdown_throttle"].update({0.4: 0.9}),
            "must not grow as drawdown deepens",
        ),
        (
            "fusion_config.yaml",
            lambda d: d["voting"]["thresholds"].update(buy=0.6),
            "strong_sell <= sell < 0 < buy <= strong_buy",
        ),
        (
            "fusion_config.yaml",
            lambda d: d["voting"]["default_weights"].update(gpt5=0.1),
            "has models not listed in `models`: ['gpt5']",
        ),
        (
            "execution_config.yaml",
            lambda d: d["position_management"].update(sl_tp_reference="open"),
            "position_management.sl_tp_reference",
        ),
        (
            "tool_config.yaml",
            lambda d: d["snapshot"].update(timeframe="M2"),
            "snapshot.timeframe",
        ),
        (
            "deepseek_config.yaml",
            lambda d: d["api"].update(base_url=""),
            "api.base_url must be set when enabled",
        ),
    ],
)
def test_semantic_rules(config_dir: Path, filename, change, expected: str) -> None:
    edit(config_dir, filename, change)
    with pytest.raises(ConfigError) as caught:
        load_config(config_dir, environ={})
    text = "\n".join(problems(caught.value))
    assert filename in text
    assert expected in text


def test_hard_limits_cannot_be_loosened_by_override() -> None:
    environ = {"MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK": str(HARD_LIMITS.max_per_trade_risk * 2)}
    with pytest.raises(ConfigError, match="per_trade_risk"):
        load_config(REPO_CONFIG, environ=environ)


def test_env_overrides_are_parsed_and_applied() -> None:
    environ = {
        "MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK": "0.005",
        "MAQ_TOOL__MT5__TERMINAL_PATH": "C:/MT5/terminal64.exe",
        "MAQ_TOOL__CORRELATION__SYMBOLS": "[EURUSD, USDJPY]",
        "MAQ_EXECUTION__DRY_RUN": "true",
        "MAQ_CONFIG_STRICT": "0",  # a plain flag, not an override
        "UNRELATED": "x",
    }
    config = load_config(REPO_CONFIG, environ=environ)
    assert config.risk.trade_level.per_trade_risk == 0.005
    assert config.tool.mt5.terminal_path == "C:/MT5/terminal64.exe"
    assert config.tool.correlation.symbols == ["EURUSD", "USDJPY"]
    assert config.execution.dry_run is True


def test_unknown_override_section_is_an_error() -> None:
    with pytest.raises(ConfigError, match=re.escape("unknown section 'risks'")):
        env_overrides({"MAQ_RISKS__TRADE_LEVEL__PER_TRADE_RISK": "0.01"})


def test_unknown_override_key_is_an_error() -> None:
    with pytest.raises(ConfigError, match=re.escape("trade_level.per_trade_riks")):
        load_config(REPO_CONFIG, environ={"MAQ_RISK__TRADE_LEVEL__PER_TRADE_RIKS": "0.01"})


def test_missing_file_is_reported(config_dir: Path) -> None:
    (config_dir / "fusion_config.yaml").unlink()
    with pytest.raises(ConfigError, match=re.escape("fusion_config.yaml: file not found")):
        load_config(config_dir, environ={})
    assert load_config(config_dir, environ={}, require_files=False).fusion.models


def test_load_section_returns_only_keys_that_are_set(tmp_path: Path) -> None:
    path = tmp_path / "execution_config.yaml"
    path.write_text("dry_run: false\nposition_management:\n  enabled: true\n", encoding="utf-8")

    data = load_section(path, environ={"MAQ_EXECUTION__MAGIC": "42"})

    # Absent keys stay absent, so legacy `cfg.get(key, default)` keeps its own default.
    assert data == {"dry_run": False, "magic": 42, "position_management": {"enabled": True}}


def test_load_section_validates(tmp_path: Path) -> None:
    path = tmp_path / "risk_config.yaml"
    path.write_text("trade_level:\n  per_trade_risk: 0.5\n", encoding="utf-8")
    with pytest.raises(
        ConfigError, match=re.escape("risk_config.yaml: trade_level.per_trade_risk")
    ):
        load_section(path, environ={})


def test_load_section_passes_unknown_files_through(tmp_path: Path) -> None:
    path = tmp_path / "something_else.yaml"
    path.write_text("anything: [1, 2]\n", encoding="utf-8")
    assert load_section(path, environ={}) == {"anything": [1, 2]}


def test_empty_files_mean_defaults(tmp_path: Path) -> None:
    for name in REPO_CONFIG.iterdir():
        (tmp_path / name.name).write_text("", encoding="utf-8")
    config = load_config(tmp_path, environ={})
    assert config.execution.dry_run is True
    assert config.risk.enforcement.enabled is True


def test_live_trading_issues_flag_unsafe_settings() -> None:
    environ = {
        "MAQ_RISK__ENFORCEMENT__ENABLED": "false",
        "MAQ_EXECUTION__USE_SIGNAL_VOLUME": "true",
        "MAQ_EXECUTION__ENFORCE_SAFETY": "false",
    }
    issues = live_trading_issues(load_config(REPO_CONFIG, environ=environ))
    assert len(issues) == 3
    assert any("enforcement.enabled is false" in issue for issue in issues)
    assert any("use_signal_volume is true" in issue for issue in issues)
    assert any("enforce_safety is false" in issue for issue in issues)
