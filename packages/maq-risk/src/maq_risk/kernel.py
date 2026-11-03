"""The risk kernel: every proposal passes through ``RiskKernel.evaluate``.

It is a pure function of (proposal, context, limits): no I/O, no clock, no
model calls. Every check runs and is recorded, so a rejection explains all of
its reasons at once, and an approval shows how the size was arrived at.

Sizing::

    capital  = min(balance, equity)          # never size off unrealised gains
    fraction = per_trade_risk x confidence factor x drawdown factor
    lots     = capital x fraction / (stop distance x value per lot + commission)

then capped by the requested volume, ``max_volume_per_trade``, the mandate,
margin headroom and the day's remaining volume, and floored onto the lot grid.
A size below the broker's minimum lot rejects the trade.
"""

from __future__ import annotations

from collections.abc import Callable

from maq_core.config.limits import HARD_LIMITS
from maq_core.domain import AccountKind, OrderType, Side
from maq_risk.limits import RiskLimits
from maq_risk.models import (
    CheckOutcome,
    Impact,
    Mandate,
    RiskContext,
    RiskDecision,
    TradeProposal,
)

_EPS = 1e-9


class RiskInvariantError(AssertionError):
    """The kernel produced a decision that breaks its own guarantees (a bug)."""


class RiskKernel:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate(self, proposal: TradeProposal, ctx: RiskContext) -> RiskDecision:
        checks: list[CheckOutcome] = []
        adjustments: list[str] = []

        def check(name: str, passed: bool, detail: Callable[[], str] | str = "") -> bool:
            text = detail() if callable(detail) and not passed else detail
            checks.append(
                CheckOutcome(name=name, passed=passed, detail="" if passed else str(text))
            )
            return passed

        limits, account, spec, tick = self.limits, ctx.account, ctx.spec, ctx.tick
        side = proposal.side
        market_entry = tick.entry_price(side)
        entry = market_entry if proposal.order_type is OrderType.MARKET else proposal.entry_price
        sl, tp = proposal.stop_loss, proposal.take_profit

        # ------------------------------------------------------------ gates
        kill = ctx.kill_switch
        check(
            "kill_switch",
            kill is None,
            lambda: f"engaged ({kill.mode}) by {kill.engaged_by}: {kill.reason}" if kill else "",
        )
        check(
            "circuit_breakers",
            not ctx.tripped_breakers,
            lambda: "; ".join(ctx.tripped_breakers),
        )
        check(
            "capital_preservation",
            not ctx.capital_preservation,
            "capital preservation mode is on",
        )
        check(
            "max_drawdown",
            ctx.drawdown < limits.max_drawdown,
            lambda: (
                f"drawdown {ctx.drawdown:.1%} from the initial balance reached "
                f"the {limits.max_drawdown:.0%} limit"
            ),
        )
        check(
            "daily_loss_limit",
            ctx.daily_pnl_fraction > -limits.daily_loss_limit,
            lambda: (
                f"down {-ctx.daily_pnl_fraction:.2%} today, limit {limits.daily_loss_limit:.2%}"
            ),
        )
        if limits.daily_profit_target is not None:
            check(
                "daily_profit_target",
                ctx.daily_pnl_fraction < limits.daily_profit_target,
                lambda: f"up {ctx.daily_pnl_fraction:.2%} today: target reached, done for the day",
            )
        if limits.max_daily_trades is not None:
            check(
                "max_daily_trades",
                ctx.trades_today < limits.max_daily_trades,
                lambda: f"{ctx.trades_today} trades today, limit {limits.max_daily_trades}",
            )
        if limits.max_daily_volume is not None:
            check(
                "max_daily_volume",
                ctx.volume_today < limits.max_daily_volume - _EPS,
                lambda: f"{ctx.volume_today} lots today, limit {limits.max_daily_volume}",
            )
        mandate = self._applicable_mandate(ctx, check)

        # ------------------------------------------------------------ the proposal itself
        check(
            "symbol",
            proposal.symbol == spec.symbol == tick.symbol,
            lambda: (
                f"proposal is for {proposal.symbol}, market data for {spec.symbol}/{tick.symbol}"
            ),
        )
        check("trading_allowed", spec.trade_allowed and account.trade_allowed, "trading disabled")
        has_stop = check("stop_loss", sl is not None, "every position needs a stop loss")
        has_entry = check(
            "entry_price",
            entry is not None,
            f"a {proposal.order_type} proposal needs an entry price",
        )
        geometry_ok = has_stop and has_entry
        if has_stop and has_entry:
            assert sl is not None and entry is not None
            geometry_ok = check(
                "stop_side",
                side.sign * (entry - sl) > _EPS,
                lambda: (
                    f"stop {sl} is not {'below' if side is Side.BUY else 'above'} the entry {entry}"
                ),
            )
        if tp is not None and entry is not None:
            target_ok = check(
                "take_profit_side",
                side.sign * (tp - entry) > _EPS,
                lambda: (
                    f"target {tp} is not {'above' if side is Side.BUY else 'below'} "
                    f"the entry {entry}"
                ),
            )
            if target_ok and geometry_ok and limits.min_reward_risk > 0:
                assert sl is not None and entry is not None
                ratio = abs(tp - entry) / abs(entry - sl)
                check(
                    "reward_risk",
                    ratio >= limits.min_reward_risk - _EPS,
                    lambda: f"reward/risk {ratio:.2f} below {limits.min_reward_risk:.2f}",
                )
        elif limits.require_take_profit:
            check("take_profit", False, "a take profit is required")

        stop_distance = abs(entry - sl) if geometry_ok and entry and sl else None
        if stop_distance is not None:
            if ctx.atr is not None:
                atrs = stop_distance / ctx.atr
                check(
                    "stop_distance_atr",
                    limits.min_stop_atr - _EPS <= atrs <= limits.max_stop_atr + _EPS,
                    lambda: (
                        f"stop is {atrs:.2f} ATR away, allowed "
                        f"{limits.min_stop_atr}-{limits.max_stop_atr}"
                    ),
                )
            check(
                "spread_to_stop",
                tick.spread <= limits.max_spread_to_stop * stop_distance + _EPS,
                lambda: (
                    f"spread {spec.price_to_pips(tick.spread):.1f} pips is more than "
                    f"{limits.max_spread_to_stop:.0%} of the {spec.price_to_pips(stop_distance):.1f}-pip stop"
                ),
            )

        # ------------------------------------------------------------ market conditions
        if limits.max_spread_pips is not None:
            spread_pips = spec.price_to_pips(tick.spread)
            check(
                "spread",
                spread_pips <= limits.max_spread_pips + _EPS,
                lambda: f"spread {spread_pips:.1f} pips, limit {limits.max_spread_pips}",
            )
        age = ctx.now - proposal.created_at
        check(
            "signal_age",
            -limits.signal_ttl < age <= limits.signal_ttl,
            lambda: (
                f"proposal is {age.total_seconds():.0f}s old, limit "
                f"{limits.signal_ttl.total_seconds():.0f}s"
            ),
        )
        if proposal.entry_price is not None:
            gap_pips = spec.price_to_pips(proposal.entry_price - market_entry)
            allowed = (
                limits.max_price_deviation_pips
                if proposal.order_type is OrderType.MARKET
                else limits.max_pending_distance_pips
            )
            check(
                "price_grounding",
                gap_pips <= allowed + _EPS,
                lambda: (
                    f"entry {proposal.entry_price} is {gap_pips:.1f} pips from the "
                    f"market {market_entry}, allowed {allowed}"
                ),
            )
        if limits.news_blackout.total_seconds() > 0:
            currencies = ctx.symbol_currencies()
            blocking = [
                event
                for event in ctx.calendar
                if event.impact is Impact.HIGH
                and event.currency in currencies
                and abs(event.time - ctx.now) <= limits.news_blackout
            ]
            check(
                "news_blackout",
                not blocking,
                lambda: ", ".join(
                    f"{e.currency} {e.title or 'event'} at {e.time:%H:%M}" for e in blocking
                ),
            )
        if ctx.market_open is not None:
            check("market_open", ctx.market_open, "market is closed")

        # ------------------------------------------------------------ portfolio
        positions = ctx.positions
        if limits.max_concurrent_positions is not None:
            check(
                "max_positions",
                len(positions) < limits.max_concurrent_positions,
                lambda: f"{len(positions)} open, limit {limits.max_concurrent_positions}",
            )
        if limits.max_positions_per_symbol is not None:
            same_symbol = sum(1 for p in positions if p.symbol == proposal.symbol)
            check(
                "max_positions_per_symbol",
                same_symbol < limits.max_positions_per_symbol,
                lambda: (
                    f"{same_symbol} open on {proposal.symbol}, "
                    f"limit {limits.max_positions_per_symbol}"
                ),
            )
        if limits.max_same_direction is not None:
            same_side = sum(1 for p in positions if p.side is side)
            check(
                "max_same_direction",
                same_side < limits.max_same_direction,
                lambda: f"{same_side} {side} positions open, limit {limits.max_same_direction}",
            )
        if limits.max_correlation is not None:
            stacked = sorted(
                {
                    p.symbol
                    for p in positions
                    if p.symbol != proposal.symbol
                    and abs(rho := ctx.correlations.get(p.symbol, 0.0)) >= limits.max_correlation
                    # Positive correlation + same side, or negative + opposite
                    # sides, adds to the same bet; the other cases hedge it.
                    and rho * p.side.sign * side.sign > 0
                }
            )
            check(
                "correlation",
                not stacked,
                lambda: f"adds to correlated exposure in {', '.join(stacked)}",
            )

        # ------------------------------------------------------------ sizing
        volume = risk_amount = risk_fraction = 0.0
        multiplier = limits.confidence_multiplier(proposal.confidence)
        check(
            "confidence",
            multiplier is not None,
            lambda: f"confidence {proposal.confidence:.2f} below {limits.min_confidence:.2f}",
        )
        if (
            stop_distance is not None
            and entry is not None
            and multiplier is not None
            and all(c.passed for c in checks)
        ):
            capital = min(account.balance, account.equity)
            drawdown_factor = limits.drawdown_multiplier(ctx.drawdown)
            base = limits.per_trade_risk
            if mandate is not None and mandate.max_risk_per_trade < base:
                base = mandate.max_risk_per_trade
                adjustments.append(f"risk per trade capped at {base:.2%} by the mandate")
            fraction = min(base * multiplier * drawdown_factor, HARD_LIMITS.max_per_trade_risk)
            if multiplier < 1.0:
                adjustments.append(f"confidence factor {multiplier:.2f}")
            if drawdown_factor < 1.0:
                adjustments.append(f"drawdown factor {drawdown_factor:.2f}")
            loss_per_lot = (
                stop_distance * spec.value_per_price_unit(1.0) + limits.commission_per_lot
            )
            caps: dict[str, float] = {
                "risk budget": max(capital, 0.0) * fraction / loss_per_lot,
                "symbol maximum": spec.volume_max,
            }
            if proposal.requested_volume is not None:
                caps["requested volume"] = proposal.requested_volume
            if limits.max_volume_per_trade is not None:
                caps["max_volume_per_trade"] = limits.max_volume_per_trade
            if mandate is not None:
                caps["mandate"] = mandate.max_volume_per_trade
                if mandate.max_total_volume is not None:
                    open_volume = sum(p.volume for p in positions)
                    caps["mandate total"] = mandate.max_total_volume - open_volume
            if limits.max_daily_volume is not None:
                caps["daily volume"] = limits.max_daily_volume - ctx.volume_today
            margin_per_lot = spec.margin(1.0, entry, account.leverage)
            headroom = min(
                limits.max_margin_usage * account.equity - account.margin, account.margin_free
            )
            caps["margin"] = max(headroom, 0.0) / margin_per_lot if margin_per_lot > 0 else 0.0

            binding = min(caps, key=lambda name: caps[name])
            volume = spec.floor_volume(caps[binding])
            if binding != "risk budget" and caps[binding] < caps["risk budget"]:
                adjustments.append(f"capped by {binding} ({caps[binding]:.4f} lots)")
            if check(
                "volume",
                volume > 0,
                lambda: (
                    f"{binding} allows {max(caps[binding], 0.0):.4f} lots, "
                    f"below the minimum lot {spec.volume_min}"
                ),
            ):
                risk_amount = volume * loss_per_lot
                risk_fraction = risk_amount / capital
                if risk_amount > capital * fraction * (1 + 1e-9) + _EPS:
                    raise RiskInvariantError(
                        f"sized {volume} lots risking {risk_amount:.2f} over the budget "
                        f"{capital * fraction:.2f}"
                    )

        approved = all(c.passed for c in checks)
        if not approved:
            volume = risk_amount = risk_fraction = 0.0
        return RiskDecision(
            approved=approved,
            symbol=proposal.symbol,
            side=side,
            order_type=proposal.order_type,
            volume=volume,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            expires_at=proposal.expires_at,
            risk_amount=risk_amount,
            risk_fraction=risk_fraction,
            checks=tuple(checks),
            adjustments=tuple(adjustments),
            evaluated_at=ctx.now,
        )

    def _applicable_mandate(
        self, ctx: RiskContext, check: Callable[[str, bool, str], bool]
    ) -> Mandate | None:
        """Real money always needs a valid mandate; demo only when configured."""
        kind = ctx.account.kind
        required = kind is AccountKind.REAL or (
            self.limits.require_mandate_for_demo and kind in (AccountKind.DEMO, AccountKind.CONTEST)
        )
        if ctx.mandate is None:
            if required:
                check("mandate", False, f"a {kind} account trades only under a valid mandate")
            return None
        problem = ctx.mandate.problem(ctx.account, ctx.spec.symbol, ctx.now)
        check("mandate", problem is None, problem or "")
        return ctx.mandate
