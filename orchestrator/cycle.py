"""Single-cycle orchestration."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.agent_loader import load_agents
from data.account_state import update_account_state
from data.agent_audit import log_agent_output
from data.correlation_builder import correlations_from_rates
from data.database import store_cycle
from data.daily_state import update_daily_state
from data.market_snapshot import build_snapshot
from data.metrics import summarize_cycle
from data.portfolio_state import get_open_orders, get_open_positions
from data.trade_history import get_deals_today
from deepseek.challenge_agent import DeepSeekChallengeAgent, apply_challenge_result
from deepseek.client import DeepSeekClient, load_deepseek_config
from deepseek.regime_analyst import DeepSeekRegimeAnalyst, build_multi_tf_data
from execution.mt5_executor import execute_signal, render_execution_result
from fusion.fusion_engine import fuse_signals
from risk.account_guard import apply_account_limits
from risk.correlation_guard import check_correlation_guard
from risk.daily_guard import check_daily_limits, compute_daily_stats
from risk.exposure_guard import check_exposure_limits
from risk.risk_guard import apply_basic_risk
from tools import mt5_connection
from tools.config_loader import load_yaml
from tools.external_plugins import collect_external_data


def run_once(
    config_dir: Path,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    bars: Optional[int] = None,
    include_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    logger = logging.getLogger(__name__)
    tool_config = load_yaml(config_dir / "tool_config.yaml")
    mt5_cfg = tool_config.get("mt5", {})
    snapshot_cfg = tool_config.get("snapshot", {})
    corr_cfg = tool_config.get("correlation", {})
    audit_cfg = tool_config.get("audit", {})
    daily_state_cfg = tool_config.get("daily_state", {})
    account_state_cfg = tool_config.get("account_state", {})
    external_cfg = tool_config.get("external_data", {})
    database_cfg = tool_config.get("database", {})

    symbol = symbol or snapshot_cfg.get("symbol") or mt5_cfg.get("default_symbol")
    timeframe = timeframe or snapshot_cfg.get("timeframe", "M1")
    bars = bars or snapshot_cfg.get("bars", 50)
    universe = snapshot_cfg.get("symbol_universe") or []
    universe_limit = snapshot_cfg.get("symbol_limit")
    include_universe = snapshot_cfg.get("include_symbol_universe", False)
    include_universe_ticks = snapshot_cfg.get("include_universe_ticks", False)
    include_positions = snapshot_cfg.get("include_positions", False)
    include_orders = snapshot_cfg.get("include_orders", False)
    include_indicators = snapshot_cfg.get("include_indicators", False)
    indicator_cfg = snapshot_cfg.get("indicators", {})

    terminal_path = mt5_cfg.get("terminal_path")
    if not terminal_path:
        raise RuntimeError("mt5.terminal_path is required in config/tool_config.yaml")

    mt5_connection.initialize(
        terminal_path=terminal_path,
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        positions = get_open_positions()
        orders = get_open_orders()
        snapshot = build_snapshot(
            symbol,
            timeframe,
            bars,
            universe=universe,
            universe_limit=universe_limit,
            include_universe=include_universe,
            include_universe_ticks=include_universe_ticks,
            positions=positions,
            orders=orders,
            include_positions=include_positions,
            include_orders=include_orders,
            include_indicators=include_indicators,
            indicator_config=indicator_cfg,
        )
        deals_today = get_deals_today()
        correlations = {}
        corr_enabled = bool(
            corr_cfg.get("enabled", False) or snapshot_cfg.get("include_correlations", False)
        )
        if corr_enabled:
            corr_symbols = corr_cfg.get("symbols") or []
            corr_tf = corr_cfg.get("timeframe", "M15")
            corr_bars = int(corr_cfg.get("bars", 120))
            if symbol not in corr_symbols:
                corr_symbols = [symbol] + list(corr_symbols)
            correlations = correlations_from_rates(corr_symbols, corr_tf, corr_bars)
            if snapshot_cfg.get("include_correlations", False):
                snapshot["correlations"] = correlations
    finally:
        mt5_connection.shutdown()

    risk_cfg = load_yaml(config_dir / "risk_config.yaml")
    account_state = None
    if account_state_cfg.get("enabled", False):
        state_path = Path(account_state_cfg.get("path", "logs/account_state.json"))
        account_state = update_account_state(
            state_path,
            snapshot.get("account", {}),
            risk_cfg.get("account_level", {}),
        )

    external_data = None
    external_errors: List[Dict[str, Any]] = []
    if external_cfg.get("enabled", False):
        ext_cfg = dict(external_cfg)
        ext_cfg.setdefault("symbol", symbol)
        external_data, external_errors = collect_external_data(ext_cfg)
        snapshot["external_data"] = external_data

    agents = load_agents(config_dir)
    if include_agents:
        allow = {name.strip() for name in include_agents if name.strip()}
        agents = [agent for agent in agents if agent.name in allow]

    if not agents:
        raise RuntimeError("No agents available for this run.")

    signals: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    audit_enabled = bool(audit_cfg.get("enabled", False))
    audit_dir = config_dir.parent / audit_cfg.get("directory", "logs/agent_outputs")

    for agent in agents:
        try:
            signal = agent.generate_signal(snapshot)
            signals.append(signal)
            logger.info("Agent %s produced %s", agent.name, signal.get("action"))
            if audit_enabled:
                prompt = getattr(agent, "last_prompt", None) or ""
                raw_output = getattr(agent, "last_raw_response", None) or ""
                reasoning = getattr(agent, "last_reasoning", None)
                repair_output = getattr(agent, "last_repair_output", None)
                log_agent_output(
                    output_dir=audit_dir,
                    agent_name=agent.name,
                    model=agent.model,
                    prompt=prompt,
                    raw_output=raw_output,
                    reasoning=reasoning,
                    max_chars=int(audit_cfg.get("max_chars", 4000)),
                    repair_output=repair_output,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent %s failed", agent.name)
            errors.append({"agent": agent.name, "error": str(exc)})
            if audit_enabled:
                prompt = getattr(agent, "last_prompt", None) or ""
                raw_output = getattr(agent, "last_raw_response", None) or ""
                reasoning = getattr(agent, "last_reasoning", None)
                repair_output = getattr(agent, "last_repair_output", None)
                log_agent_output(
                    output_dir=audit_dir,
                    agent_name=agent.name,
                    model=agent.model,
                    prompt=prompt,
                    raw_output=raw_output,
                    reasoning=reasoning,
                    max_chars=int(audit_cfg.get("max_chars", 4000)),
                    error=str(exc),
                    repair_output=repair_output,
                )

    if not signals:
        raise RuntimeError("All agents failed; no signals to fuse.")

    fusion_cfg = load_yaml(config_dir / "fusion_config.yaml")
    fused = fuse_signals(signals, fusion_cfg)

    enforcement = risk_cfg.get("enforcement", {}).get("enabled", True)
    final_signal = fused["representative_signal"]
    deepseek_cfg = load_deepseek_config(config_dir)
    auxiliary_cfg = deepseek_cfg.get("auxiliary", {}) if deepseek_cfg else {}
    challenge_cfg = auxiliary_cfg.get("challenge_agent", {}) if auxiliary_cfg else {}
    regime_cfg = auxiliary_cfg.get("regime_analysis", {}) if auxiliary_cfg else {}

    challenge_result = None
    regime_result = None
    deepseek_client: DeepSeekClient | None = None

    def should_trigger_challenge() -> bool:
        min_ratio = float(challenge_cfg.get("min_agreement_ratio", 1.0))
        if fused.get("agreement_ratio", 1.0) < min_ratio:
            return True
        if challenge_cfg.get("trigger_on_regime_disagreement", True) and not fused.get(
            "regime_ok", True
        ):
            return True
        return False

    if deepseek_cfg.get("enabled", False) and challenge_cfg.get("enabled", False):
        if should_trigger_challenge():
            try:
                deepseek_client = DeepSeekClient(deepseek_cfg, agent_name="deepseek_aux")
                challenge_agent = DeepSeekChallengeAgent(deepseek_client, challenge_cfg)
                challenge_result = challenge_agent.challenge_decision(
                    fusion_result=fused,
                    signals=signals,
                    snapshot=snapshot,
                )
                parsed = challenge_result.get("parsed")
                if parsed:
                    final_signal = apply_challenge_result(final_signal, parsed, challenge_cfg)
            except Exception as exc:  # noqa: BLE001
                errors.append({"agent": "deepseek_aux", "error": str(exc)})

    if deepseek_cfg.get("enabled", False) and regime_cfg.get("enabled", False):
        trigger_on_unclear = regime_cfg.get("trigger_on_unclear", True)
        if trigger_on_unclear and not fused.get("regime_ok", True):
            try:
                if deepseek_client is None:
                    deepseek_client = DeepSeekClient(deepseek_cfg, agent_name="deepseek_aux")
                regime_agent = DeepSeekRegimeAnalyst(deepseek_client)
                multi_tf_data = build_multi_tf_data(symbol, mt5_cfg, regime_cfg)
                regime_result = regime_agent.analyze_regime(symbol, multi_tf_data)
            except Exception as exc:  # noqa: BLE001
                errors.append({"agent": "deepseek_aux", "error": str(exc)})
    risk_notes: List[str] = []
    if enforcement:
        final_signal, risk_notes = apply_basic_risk(
            fused["representative_signal"], risk_cfg
        )
        final_signal, account_notes = apply_account_limits(
            final_signal, account_state, risk_cfg.get("account_level", {})
        )
        risk_notes.extend(account_notes)
        exposure_notes = check_exposure_limits(
            final_signal, positions, risk_cfg.get("account_level", {})
        )
        if exposure_notes:
            final_signal = {**final_signal, "action": "HOLD"}
            risk_notes.extend(exposure_notes)

        corr_threshold = risk_cfg.get("account_level", {}).get("max_correlation")
        correlation_notes: List[str] = []
        if corr_threshold is not None and correlations:
            correlation_notes = check_correlation_guard(
                final_signal, correlations, positions, float(corr_threshold)
            )
            if correlation_notes:
                final_signal = {**final_signal, "action": "HOLD"}
                risk_notes.extend(correlation_notes)

    daily_stats = compute_daily_stats(deals_today)
    if enforcement:
        daily_notes = check_daily_limits(
            final_signal, daily_stats, risk_cfg.get("daily_level", {})
        )
        if daily_notes:
            final_signal = {**final_signal, "action": "HOLD"}
            risk_notes.extend(daily_notes)

    daily_state = None
    if daily_state_cfg.get("enabled", False):
        state_path = Path(daily_state_cfg.get("path", "logs/daily_state.json"))
        daily_state = update_daily_state(state_path, daily_stats)

    execution_cfg = load_yaml(config_dir / "execution_config.yaml")
    execution_result = None
    if execution_cfg.get("enabled", False):
        execution = execute_signal(final_signal, execution_cfg, mt5_cfg, positions=positions)
        execution_result = render_execution_result(execution)

    result = {
        "snapshot_meta": {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": bars,
            "timestamp": snapshot.get("timestamp"),
        },
        "positions": positions,
        "signals": signals,
        "errors": errors,
        "external_errors": external_errors,
        "correlations": correlations,
        "account_state": account_state,
        "daily_stats": daily_stats,
        "daily_state": daily_state,
        "fusion": fused,
        "challenge_review": challenge_result,
        "regime_analysis": regime_result,
        "final_signal": final_signal,
        "risk_notes": risk_notes,
        "execution": execution_result,
    }
    result["summary"] = summarize_cycle(result)

    if database_cfg.get("enabled", False):
        db_path = Path(database_cfg.get("path", "logs/analytics.db"))
        try:
            store_cycle(result, db_path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"agent": "database", "error": str(exc)})
    return result
