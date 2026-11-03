"""Load, validate and override the YAML files in a config directory.

Layering, lowest to highest priority:

1. Defaults in the schema (``maq_core.config.models``).
2. The YAML files: ``tool_config.yaml``, ``risk_config.yaml``, ...
3. Environment variables ``MAQ_<SECTION>__<KEY>__<SUBKEY>=value``, for example
   ``MAQ_TOOL__MT5__TERMINAL_PATH=C:/MT5/terminal64.exe`` or
   ``MAQ_RISK__TRADE_LEVEL__PER_TRADE_RISK=0.005``. Values are parsed as YAML,
   so numbers, booleans, ``null`` and ``[lists]`` work; quote anything ambiguous.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from maq_core.config.models import SECTION_MODELS, AppConfig

ENV_PREFIX = "MAQ_"
ENV_DELIMITER = "__"

SECTION_FILES: dict[str, str] = {
    "tool": "tool_config.yaml",
    "model": "model_config.yaml",
    "deepseek": "deepseek_config.yaml",
    "fusion": "fusion_config.yaml",
    "risk": "risk_config.yaml",
    "execution": "execution_config.yaml",
    "validation": "validation_config.yaml",
}
FILE_SECTIONS: dict[str, str] = {name: section for section, name in SECTION_FILES.items()}


@dataclass(frozen=True)
class ConfigIssue:
    """One validation problem, located by file and dotted key path."""

    source: str
    location: str
    message: str

    def __str__(self) -> str:
        where = f"{self.source}: {self.location}" if self.location else self.source
        return f"{where}: {self.message}"


class ConfigError(ValueError):
    """Raised when configuration files or overrides are invalid."""

    def __init__(self, issues: list[ConfigIssue]) -> None:
        self.issues = issues
        lines = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(f"Invalid configuration ({len(issues)} problem(s)):\n{lines}")


# --------------------------------------------------------------------------- helpers
def read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError([ConfigIssue(path.name, "", "top level must be a mapping")])
    return data


def deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def env_overrides(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Collect ``MAQ_SECTION__KEY__...`` variables into ``{section: nested dict}``."""
    environ = os.environ if environ is None else environ
    result: dict[str, dict[str, Any]] = {}
    issues: list[ConfigIssue] = []
    for name, raw in environ.items():
        if not name.upper().startswith(ENV_PREFIX) or ENV_DELIMITER not in name:
            continue  # MAQ_CONFIG_STRICT and other plain flags are not overrides
        parts = [part.lower() for part in name[len(ENV_PREFIX) :].split(ENV_DELIMITER)]
        section, keys = parts[0], parts[1:]
        if section not in SECTION_FILES:
            issues.append(
                ConfigIssue(
                    name,
                    "",
                    f"unknown section '{section}', expected one of {sorted(SECTION_FILES)}",
                )
            )
            continue
        if not keys or not all(keys):
            issues.append(ConfigIssue(name, "", "expected MAQ_<SECTION>__<KEY>[__<SUBKEY>...]"))
            continue
        node = result.setdefault(section, {})
        for key in keys[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                child = node[key] = {}
            node = child
        try:
            node[keys[-1]] = yaml.safe_load(raw) if raw.strip() else ""
        except yaml.YAMLError:
            node[keys[-1]] = raw
    if issues:
        raise ConfigError(issues)
    return result


def _issues_from(error: ValidationError, source: str) -> list[ConfigIssue]:
    issues = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        message = item["msg"]
        if item.get("type") not in ("missing", "extra_forbidden") and "input" in item:
            message = f"{message} (got {item['input']!r})"
        issues.append(ConfigIssue(source, location, message))
    return issues


# --------------------------------------------------------------------------- public API
def load_config(
    config_dir: Path | str,
    environ: Mapping[str, str] | None = None,
    require_files: bool = True,
) -> AppConfig:
    """Validate every file of ``config_dir`` (plus overrides) and return the typed config."""
    config_dir = Path(config_dir)
    overrides = env_overrides(environ)
    data: dict[str, Any] = {}
    issues: list[ConfigIssue] = []
    for section, filename in SECTION_FILES.items():
        path = config_dir / filename
        if path.exists():
            raw = read_yaml(path)
        elif require_files:
            issues.append(ConfigIssue(filename, "", f"file not found in {config_dir}"))
            continue
        else:
            raw = {}
        data[section] = deep_merge(raw, overrides.get(section, {}))

    if issues:
        raise ConfigError(issues)
    try:
        return AppConfig.model_validate(data)
    except ValidationError as error:
        located = []
        for issue in _issues_from(error, "config"):
            section, _, rest = issue.location.partition(".")
            located.append(ConfigIssue(SECTION_FILES.get(section, section), rest, issue.message))
        raise ConfigError(located) from None


def load_section(path: Path | str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Validate one config file and return it as a plain dict, overrides applied.

    Only keys that are set in the file or by an override are returned, so legacy
    code calling ``cfg.get(key, default)`` keeps its own defaults for absent keys.
    Files that are not one of the known config files are returned as parsed.
    """
    path = Path(path)
    data = read_yaml(path)
    section = FILE_SECTIONS.get(path.name)
    if section is None:
        return data
    merged = deep_merge(data, env_overrides(environ).get(section, {}))
    try:
        model = SECTION_MODELS[section].model_validate(merged)
    except ValidationError as error:
        raise ConfigError(_issues_from(error, path.name)) from None
    return model.model_dump(exclude_unset=True)


def live_trading_issues(config: AppConfig) -> list[str]:
    """Settings that are acceptable for experiments but must not reach a live account."""
    issues = []
    risk, execution = config.risk, config.execution
    if not risk.enforcement.enabled:
        issues.append(
            "risk_config.yaml: enforcement.enabled is false -- drawdown, daily and exposure limits are skipped"
        )
    if risk.trade_level.per_trade_risk is None:
        issues.append("risk_config.yaml: trade_level.per_trade_risk is not set")
    if not risk.trade_level.require_stop_loss:
        issues.append("risk_config.yaml: trade_level.require_stop_loss is false")
    if risk.account_level.max_drawdown is None:
        issues.append("risk_config.yaml: account_level.max_drawdown (hard stop) is not set")
    if risk.daily_level.daily_loss_limit is None:
        issues.append("risk_config.yaml: daily_level.daily_loss_limit is not set")
    if execution.use_signal_volume:
        issues.append(
            "execution_config.yaml: use_signal_volume is true -- the LLM picks the lot size"
        )
    if not execution.use_risk_sizing and not execution.use_signal_volume:
        issues.append(
            "execution_config.yaml: use_risk_sizing is false -- every order uses default_volume"
        )
    if not execution.enforce_safety:
        issues.append(
            "execution_config.yaml: enforce_safety is false -- spread/liquidity checks are skipped"
        )
    return issues
