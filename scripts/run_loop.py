"""
Multi-Agent Quantitative Trading Loop
=====================================
A real hedge fund simulation with:
- Research Department: 4 LLMs analyze markets independently
- Trading Department: 1 LLM executes with full MT5 tool access
- Full market freedom: agents can trade any symbol
- Continuous operation: runs until stopped
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from agents.agent_loader import load_agents
from agents.trading_agent import create_trading_agent
from data.account_state import update_account_state
from data.agent_audit import log_agent_output
from data.correlation_builder import correlations_from_rates
from data.cycle_store import save_cycle
from data.daily_state import update_daily_state
from data.market_snapshot import build_snapshot
from data.metrics import summarize_cycle
from data.portfolio_state import get_open_positions, get_open_orders
from data.trade_history import get_deals_today
from execution.mt5_executor import execute_signal
from fusion.fusion_engine import fuse_signals
from risk.account_guard import apply_account_limits
from risk.correlation_guard import check_correlation_guard
from risk.daily_guard import check_daily_limits, compute_daily_stats
from risk.exposure_guard import check_exposure_limits
from risk.risk_guard import apply_basic_risk
from tools import mt5_connection
from tools.config_loader import load_yaml
from tools.logging_setup import setup_logging
from tools.mt5_tools import get_account_info, get_symbols
from tools.external_plugins import collect_external_data


# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_RESEARCH_AGENTS = ["gpt", "claude", "gemini", "deepseek"]
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_TIMEFRAME = "M5"
DEFAULT_BARS = 100
MAX_SYMBOLS_PER_SCAN = 20  # Limit symbols per cycle to manage API costs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-Agent Hedge Fund Trading Loop"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MINUTES,
        help=f"Minutes between cycles (default: {DEFAULT_INTERVAL_MINUTES})",
    )
    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        help=f"Analysis timeframe (default: {DEFAULT_TIMEFRAME})",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        help="Stop after N cycles (for testing)",
    )
    return parser.parse_args()


# =============================================================================
# MARKET SCANNER
# =============================================================================

def scan_market_opportunities(mt5_cfg: Dict, logger: logging.Logger) -> List[str]:
    """
    Scan all available symbols and return top opportunities.
    A real hedge fund would have sophisticated screening here.
    """
    logger.info("[SCANNER] Scanning market for opportunities...")

    mt5_connection.initialize(
        terminal_path=mt5_cfg.get("terminal_path"),
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        all_symbols = get_symbols()
    finally:
        mt5_connection.shutdown()

    if not all_symbols:
        logger.warning("[SCANNER] No symbols available")
        return []

    # Filter for tradeable symbols with reasonable spreads
    tradeable = []
    for sym in all_symbols:
        # Basic filters
        if not sym.get("visible", False):
            continue
        if sym.get("trade_mode", 0) == 0:  # TRADE_MODE_DISABLED
            continue

        name = sym.get("name", "")

        # Skip special contract variants (e.g., EURUSD99, GBPUSD.i)
        if re.search(r'\d{2}$', name) or '.' in name or '#' in name:
            continue

        # Prefer forex majors and popular instruments
        spread = sym.get("spread", 999)

        # Priority scoring
        score = 0
        if any(major in name for major in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]):
            score += 100
        if any(minor in name for minor in ["AUDUSD", "NZDUSD", "USDCAD", "EURJPY", "GBPJPY"]):
            score += 50
        if spread < 30:
            score += 30
        if spread < 10:
            score += 20

        if score > 0:
            tradeable.append((name, score, spread))

    # Sort by score and take top symbols
    tradeable.sort(key=lambda x: (-x[1], x[2]))
    selected = [t[0] for t in tradeable[:MAX_SYMBOLS_PER_SCAN]]

    logger.info("[SCANNER] Selected %d symbols: %s", len(selected), selected[:5])
    return selected


# =============================================================================
# RESEARCH DEPARTMENT
# =============================================================================

def run_research_department(
    config_dir: Path,
    symbol: str,
    timeframe: str,
    bars: int,
    mt5_cfg: Dict,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """
    Research Department: All available LLMs analyze the symbol independently.
    If an agent fails (rate limit, error), treat as "on vacation".
    """
    logger.info("[RESEARCH] Analyzing %s with all available analysts...", symbol)

    # Load all configured agents
    agents = load_agents(config_dir)
    if not agents:
        return {"error": "No research agents available", "signals": []}

    # Get market snapshot
    mt5_connection.initialize(
        terminal_path=mt5_cfg.get("terminal_path"),
        timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
    )
    try:
        positions = get_open_positions()
        orders = get_open_orders()
        snapshot = build_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            include_positions=True,
            positions=positions,
            orders=orders,
        )
    finally:
        mt5_connection.shutdown()

    # Collect signals from all agents
    signals: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    active_analysts = []
    vacation_analysts = []

    tool_cfg = load_yaml(config_dir / "tool_config.yaml")
    external_cfg = tool_cfg.get("external_data", {})
    audit_cfg = tool_cfg.get("audit", {})
    audit_enabled = audit_cfg.get("enabled", False)
    audit_dir = config_dir.parent / audit_cfg.get("directory", "logs/agent_outputs")

    external_errors: List[Dict[str, Any]] = []
    if external_cfg.get("enabled", False):
        ext_cfg = dict(external_cfg)
        ext_cfg.setdefault("symbol", symbol)
        external_data, external_errors = collect_external_data(ext_cfg)
        snapshot["external_data"] = external_data

    for agent in agents:
        try:
            signal = agent.generate_signal(snapshot)
            signals.append(signal)
            active_analysts.append(agent.name)
            logger.info("  [%s] %s (conf=%.2f)",
                       agent.name.upper(),
                       signal.get("action"),
                       signal.get("confidence", 0))

            if audit_enabled:
                log_agent_output(
                    output_dir=audit_dir,
                    agent_name=agent.name,
                    model=agent.model,
                    prompt=getattr(agent, "last_prompt", ""),
                    raw_output=getattr(agent, "last_raw_response", ""),
                    max_chars=audit_cfg.get("max_chars", 4000),
                )

        except Exception as exc:
            vacation_analysts.append(agent.name)
            errors.append({"agent": agent.name, "error": str(exc)})
            logger.warning("  [%s] On vacation (error: %s)", agent.name.upper(), str(exc)[:50])

            if audit_enabled:
                log_agent_output(
                    output_dir=audit_dir,
                    agent_name=agent.name,
                    model=agent.model,
                    prompt=getattr(agent, "last_prompt", ""),
                    raw_output=getattr(agent, "last_raw_response", ""),
                    max_chars=audit_cfg.get("max_chars", 4000),
                    error=str(exc),
                )

    logger.info("[RESEARCH] Active: %s | On vacation: %s",
               active_analysts or "None",
               vacation_analysts or "None")

    if not signals:
        return {
            "symbol": symbol,
            "error": "All analysts on vacation",
            "signals": [],
            "errors": errors,
            "snapshot": snapshot,
        }

    # Fuse signals
    fusion_cfg = load_yaml(config_dir / "fusion_config.yaml")
    fused = fuse_signals(signals, fusion_cfg)

    logger.info("[RESEARCH] Fusion: %s (agreement=%.2f, score=%.2f)",
               fused.get("action"),
               fused.get("agreement_ratio", 0),
               fused.get("directional_score", 0))

    return {
        "symbol": symbol,
        "snapshot": snapshot,
        "signals": signals,
        "errors": errors,
        "external_errors": external_errors,
        "fusion": fused,
        "active_analysts": active_analysts,
        "vacation_analysts": vacation_analysts,
        "positions": positions,
    }


# =============================================================================
# TRADING DEPARTMENT
# =============================================================================

def run_trading_department(
    research_result: Dict[str, Any],
    final_signal: Dict[str, Any],
    trading_agent,
    mt5_cfg: Dict,
    logger: logging.Logger,
    account_info: Optional[Dict[str, Any]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Trading Department: Dedicated LLM with MT5 tool access.
    Decides whether to execute and with what parameters.
    """
    fusion = research_result.get("fusion", {})
    action = final_signal.get("action", "HOLD")
    symbol = final_signal.get("symbol") or research_result.get("symbol")

    if action == "HOLD":
        logger.info("[TRADING] Research says HOLD, no action needed")
        return {
            "decision": "SKIP",
            "reason": "Research department recommends HOLD",
        }

    logger.info("[TRADING] Processing %s signal for %s...", action, symbol)

    if account_info is None or positions is None:
        mt5_connection.initialize(
            terminal_path=mt5_cfg.get("terminal_path"),
            timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
        )
        try:
            account_info = get_account_info()
            positions = get_open_positions()
        finally:
            mt5_connection.shutdown()

    # Build signal for trading agent
    research_signal = dict(final_signal)
    research_signal["symbol"] = symbol
    research_signal["action"] = action
    ensemble_confidence = fusion.get("average_confidence")
    if ensemble_confidence is not None:
        research_signal["confidence"] = ensemble_confidence
    research_signal["agreement_ratio"] = fusion.get("agreement_ratio", 0)

    try:
        decision = trading_agent.process_signal(
            research_signal=research_signal,
            account_info=account_info,
            positions=positions,
        )
    except Exception as exc:
        logger.exception("[TRADING] Agent error")
        decision = {
            "decision": "REJECT",
            "reason": f"Trading agent error: {exc}",
        }

    logger.info("[TRADING] Decision: %s - %s",
               decision.get("decision"),
               decision.get("reason", "")[:100])

    if decision.get("order"):
        order = decision["order"]
        logger.info("[TRADING] Order: %s %s %.4f lots",
                   order.get("action"),
                   order.get("symbol"),
                   order.get("volume", 0))

    return decision


# =============================================================================
# EXECUTION
# =============================================================================

def execute_trade(
    trading_decision: Dict[str, Any],
    config_dir: Path,
    mt5_cfg: Dict,
    positions: List[Dict],
    logger: logging.Logger,
) -> Dict[str, Any]:
    """
    Execute the trade via MT5.
    """
    if trading_decision.get("decision") not in ("EXECUTE", "MODIFY"):
        return {"status": "NOT_EXECUTED", "reason": trading_decision.get("reason")}

    order = trading_decision.get("order")
    if not order:
        return {"status": "NO_ORDER", "reason": "Trading decision missing order details"}

    logger.info("[EXECUTION] Executing order...")

    # Convert trading department order to execution format
    signal = {
        "symbol": order.get("symbol"),
        "action": order.get("action"),
        "volume": order.get("volume"),
        "entry": {"type": "market", "price": None},
        "risk": {
            "stop_loss_pips": 0,  # Trading dept provides actual prices
            "take_profit_pips": 0,
        },
    }

    # Add SL/TP if provided as prices (need to convert or pass through)
    if order.get("sl_price"):
        signal["sl_price"] = order["sl_price"]
    if order.get("tp_price"):
        signal["tp_price"] = order["tp_price"]

    execution_cfg = load_yaml(config_dir / "execution_config.yaml")
    execution_cfg["enabled"] = True  # Always execute in this mode
    execution_cfg["dry_run"] = False  # No dry run - it's a demo account

    result = execute_signal(signal, execution_cfg, mt5_cfg, positions=positions)

    logger.info("[EXECUTION] Result: %s", result.status if hasattr(result, 'status') else result)

    return {
        "status": result.status if hasattr(result, 'status') else "UNKNOWN",
        "order_request": result.order_request if hasattr(result, 'order_request') else None,
        "order_response": result.order_response if hasattr(result, 'order_response') else None,
        "management": result.management if hasattr(result, 'management') else None,
        "errors": result.errors if hasattr(result, 'errors') else None,
        "notes": result.notes if hasattr(result, 'notes') else None,
    }


# =============================================================================
# MAIN LOOP
# =============================================================================

def next_run_time(interval_minutes: int) -> datetime:
    now = datetime.now(timezone.utc)
    interval_seconds = max(1, interval_minutes) * 60
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = (now - epoch).total_seconds()
    next_seconds = (math.floor(elapsed / interval_seconds) + 1) * interval_seconds
    return epoch + timedelta(seconds=next_seconds)


def main() -> None:
    args = parse_args()
    config_dir = repo_root / "config"

    # Setup logging
    tool_cfg = load_yaml(config_dir / "tool_config.yaml")
    risk_cfg = load_yaml(config_dir / "risk_config.yaml")
    logging_cfg = tool_cfg.get("logging", {})
    mt5_cfg = tool_cfg.get("mt5", {})
    log_dir = repo_root / logging_cfg.get("directory", "logs")
    setup_logging(log_dir, logging_cfg.get("level", "INFO"))

    logger = logging.getLogger(__name__)

    # Banner
    logger.info("=" * 70)
    logger.info("  MULTI-AGENT QUANTITATIVE HEDGE FUND")
    logger.info("  Research Department: GPT + Claude + Gemini + DeepSeek")
    logger.info("  Trading Department: DeepSeek with MT5 Tool Access")
    logger.info("  Mode: LIVE DEMO TRADING (no dry-run)")
    logger.info("=" * 70)

    # Create trading agent
    trading_agent = create_trading_agent(config_dir, mt5_cfg)
    if trading_agent is None:
        logger.error("Failed to create Trading Agent. Check config/deepseek_config.yaml")
        return

    cycle_dir = repo_root / logging_cfg.get("cycle_dir", "logs/cycles")
    cycle_count = 0

    try:
        while True:
            cycle_start = datetime.now(timezone.utc)
            cycle_count += 1

            logger.info("\n" + "=" * 70)
            logger.info("CYCLE %d | %s", cycle_count, cycle_start.strftime("%Y-%m-%d %H:%M:%S UTC"))
            logger.info("=" * 70)

            # Step 1: Scan market for opportunities
            symbols = scan_market_opportunities(mt5_cfg, logger)

            if not symbols:
                logger.warning("No tradeable symbols found. Waiting for next cycle.")
            else:
                # Step 2: For each symbol, run full pipeline
                for symbol in symbols:
                    try:
                        # Research Department
                        research_result = run_research_department(
                            config_dir=config_dir,
                            symbol=symbol,
                            timeframe=args.timeframe,
                            bars=DEFAULT_BARS,
                            mt5_cfg=mt5_cfg,
                            logger=logger,
                        )

                        if research_result.get("error"):
                            logger.error("[%s] Research error: %s", symbol, research_result["error"])
                            snapshot = research_result.get("snapshot", {})
                            error_payload = dict(research_result)
                            error_payload["snapshot_meta"] = {
                                "symbol": symbol,
                                "timeframe": snapshot.get("timeframe", args.timeframe),
                                "bars": snapshot.get("bars", DEFAULT_BARS),
                                "timestamp": snapshot.get("timestamp"),
                            }
                            save_cycle(error_payload, cycle_dir)
                            continue

                        account_info = None
                        positions: List[Dict[str, Any]] = []
                        deals_today: List[Dict[str, Any]] = []
                        correlations: Dict[str, Any] = {}

                        mt5_connection.initialize(
                            terminal_path=mt5_cfg.get("terminal_path"),
                            timeout_seconds=mt5_cfg.get("initialize_timeout_seconds", 30),
                        )
                        try:
                            account_info = get_account_info()
                            positions = get_open_positions()
                            deals_today = get_deals_today()
                            corr_cfg = tool_cfg.get("correlation", {})
                            if corr_cfg.get("enabled", False):
                                corr_symbols = corr_cfg.get("symbols") or []
                                corr_tf = corr_cfg.get("timeframe", "M15")
                                corr_bars = int(corr_cfg.get("bars", 120))
                                if symbol not in corr_symbols:
                                    corr_symbols = [symbol] + list(corr_symbols)
                                correlations = correlations_from_rates(
                                    corr_symbols,
                                    corr_tf,
                                    corr_bars,
                                )
                        finally:
                            mt5_connection.shutdown()

                        account_state = None
                        account_state_cfg = tool_cfg.get("account_state", {})
                        if account_state_cfg.get("enabled", False):
                            state_path = repo_root / account_state_cfg.get(
                                "path", "logs/account_state.json"
                            )
                            account_state = update_account_state(
                                state_path,
                                account_info or {},
                                risk_cfg.get("account_level", {}),
                            )

                        daily_stats = compute_daily_stats(deals_today)
                        daily_state = None
                        daily_state_cfg = tool_cfg.get("daily_state", {})
                        if daily_state_cfg.get("enabled", False):
                            state_path = repo_root / daily_state_cfg.get(
                                "path", "logs/daily_state.json"
                            )
                            daily_state = update_daily_state(state_path, daily_stats)

                        final_signal = research_result.get("fusion", {}).get(
                            "representative_signal", {}
                        )
                        risk_notes: List[str] = []
                        if risk_cfg.get("enforcement", {}).get("enabled", True):
                            final_signal, risk_notes = apply_basic_risk(
                                final_signal, risk_cfg
                            )
                            final_signal, account_notes = apply_account_limits(
                                final_signal,
                                account_state,
                                risk_cfg.get("account_level", {}),
                            )
                            risk_notes.extend(account_notes)
                            exposure_notes = check_exposure_limits(
                                final_signal,
                                positions,
                                risk_cfg.get("account_level", {}),
                            )
                            if exposure_notes:
                                final_signal = {**final_signal, "action": "HOLD"}
                                risk_notes.extend(exposure_notes)

                            corr_threshold = risk_cfg.get("account_level", {}).get(
                                "max_correlation"
                            )
                            if corr_threshold is not None and correlations:
                                correlation_notes = check_correlation_guard(
                                    final_signal,
                                    correlations,
                                    positions,
                                    float(corr_threshold),
                                )
                                if correlation_notes:
                                    final_signal = {**final_signal, "action": "HOLD"}
                                    risk_notes.extend(correlation_notes)

                            daily_notes = check_daily_limits(
                                final_signal, daily_stats, risk_cfg.get("daily_level", {})
                            )
                            if daily_notes:
                                final_signal = {**final_signal, "action": "HOLD"}
                                risk_notes.extend(daily_notes)

                        # Trading Department
                        trading_decision = run_trading_department(
                            research_result=research_result,
                            final_signal=final_signal,
                            trading_agent=trading_agent,
                            mt5_cfg=mt5_cfg,
                            logger=logger,
                            account_info=account_info,
                            positions=positions,
                        )

                        # Execution
                        execution_result = execute_trade(
                            trading_decision=trading_decision,
                            config_dir=config_dir,
                            mt5_cfg=mt5_cfg,
                            positions=positions,
                            logger=logger,
                        )

                        snapshot = research_result.get("snapshot", {})
                        full_result = {
                            "snapshot_meta": {
                                "symbol": symbol,
                                "timeframe": snapshot.get("timeframe", args.timeframe),
                                "bars": snapshot.get("bars", DEFAULT_BARS),
                                "timestamp": snapshot.get("timestamp"),
                            },
                            "positions": positions,
                            "signals": research_result.get("signals"),
                            "errors": research_result.get("errors"),
                            "external_errors": research_result.get("external_errors", []),
                            "correlations": correlations,
                            "account_state": account_state,
                            "daily_stats": daily_stats,
                            "daily_state": daily_state,
                            "fusion": research_result.get("fusion"),
                            "final_signal": final_signal,
                            "risk_notes": risk_notes,
                            "execution": execution_result,
                            "trading": trading_decision,
                        }
                        full_result["summary"] = summarize_cycle(full_result)

                        save_cycle(full_result, cycle_dir)

                        # Summary
                        print(json.dumps({
                            "cycle": cycle_count,
                            "symbol": symbol,
                            "research_action": research_result.get("fusion", {}).get("action"),
                            "final_action": final_signal.get("action"),
                            "trading_decision": trading_decision.get("decision"),
                            "execution_status": execution_result.get("status"),
                        }, indent=2))

                    except Exception as exc:
                        logger.exception("[%s] Cycle error", symbol)
                        save_cycle({"symbol": symbol, "error": str(exc)}, cycle_dir)

            # Check max cycles
            if args.max_cycles and cycle_count >= args.max_cycles:
                logger.info("Max cycles (%d) reached. Shutting down.", args.max_cycles)
                break

            # Sleep until next interval
            sleep_until = next_run_time(args.interval)
            sleep_seconds = (sleep_until - datetime.now(timezone.utc)).total_seconds()
            if sleep_seconds > 0:
                logger.info("\nSleeping %.0f seconds until next cycle...\n", sleep_seconds)
                time.sleep(sleep_seconds)

    except KeyboardInterrupt:
        logger.info("\n\nShutdown requested (Ctrl+C). Goodbye!")


if __name__ == "__main__":
    main()
