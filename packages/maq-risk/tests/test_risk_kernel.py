"""RiskKernel: every check has a passing and a failing case; sizing is exact."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from maq_core.config import load_config
from maq_core.domain import (
    AccountKind,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Tick,
)
from maq_core.events import parse_event
from maq_risk import (
    EconomicEvent,
    Impact,
    KillSwitchMode,
    KillSwitchState,
    Mandate,
    RiskDecision,
    RiskDecisionMade,
    RiskKernel,
    RiskLimits,
)
from maq_risk.testing import ASK, BID, EURUSD, T0, make_account, make_context, make_proposal

KERNEL = RiskKernel(RiskLimits())


def evaluate(limits: RiskLimits | None = None, **ctx: Any) -> Any:
    def run(**proposal: Any) -> RiskDecision:
        kernel = RiskKernel(limits) if limits else KERNEL
        return kernel.evaluate(make_proposal(**proposal), make_context(**ctx))

    return run


def failed(decision: RiskDecision) -> set[str]:
    return {check.name for check in decision.failed_checks}


def position(symbol: str = "EURUSD", side: Side = Side.BUY, volume: float = 0.1) -> Position:
    return Position(
        position_id=f"{symbol}-{side}-{volume}",
        symbol=symbol,
        side=side,
        volume=volume,
        open_price=1.1,
        open_time=T0 - timedelta(hours=1),
    )


# ============================================================================ sizing
class TestSizing:
    def test_healthy_proposal_is_sized_from_the_risk_budget(self) -> None:
        decision = evaluate()()
        assert decision.approved, decision.reasons
        # 1% of 10,000 = 100; stop 21 points from the ask = 210 per lot -> 0.476 -> 0.47
        assert decision.volume == pytest.approx(0.47)
        assert decision.risk_amount == pytest.approx(0.47 * 210)
        assert decision.risk_fraction == pytest.approx(0.47 * 210 / 10_000)
        assert decision.entry_price == ASK
        assert decision.adjustments == ()

    def test_the_m3_scenario_ten_lots_without_a_stop_is_rejected(self) -> None:
        decision = evaluate()(stop_loss=None, requested_volume=10)
        assert not decision.approved
        assert "stop_loss" in failed(decision)
        assert decision.volume == 0

    def test_asking_for_ten_lots_gets_the_risk_budget(self) -> None:
        decision = evaluate()(requested_volume=10)
        assert decision.approved
        assert decision.volume == pytest.approx(0.47)

    def test_a_smaller_request_shrinks_the_size(self) -> None:
        decision = evaluate()(requested_volume=0.2)
        assert decision.volume == pytest.approx(0.2)
        assert any("requested volume" in a for a in decision.adjustments)

    def test_confidence_scales_the_budget(self) -> None:
        # min_confidence 0.5 -> x0.5, 0.75 -> x0.75
        assert evaluate()(confidence=0.75).volume == pytest.approx(0.35)
        assert evaluate()(confidence=0.5).volume == pytest.approx(0.23)
        low = evaluate()(confidence=0.49)
        assert "confidence" in failed(low)

    def test_drawdown_throttle(self) -> None:
        limits = RiskLimits(drawdown_throttle=((0.10, 0.75), (0.25, 0.5)))
        # 12,000 initial, 10,000 now: 16.7% down -> x0.75 of 100 -> 0.357 -> 0.35
        decision = evaluate(limits, initial_balance=12_000)()
        assert decision.volume == pytest.approx(0.35)
        assert "drawdown factor 0.75" in decision.adjustments

    def test_sizes_off_the_lower_of_balance_and_equity(self) -> None:
        richer_on_paper = evaluate(account=make_account(equity=20_000))()
        assert richer_on_paper.volume == pytest.approx(0.47)  # balance 10,000 still rules
        poorer = evaluate(
            account=make_account(equity=5_000), initial_balance=5_000, day_start_balance=5_000
        )()
        assert poorer.volume == pytest.approx(0.23)

    def test_commission_counts_as_risk(self) -> None:
        limits = RiskLimits(commission_per_lot=40.0)  # 250 per lot at the stop
        assert evaluate(limits)().volume == pytest.approx(0.4)

    def test_margin_headroom_caps_the_size(self) -> None:
        # max_margin_usage 0.5 of 10,000 = 5,000; 4,900 used; 1 lot needs ~1,100.
        decision = evaluate(account=make_account(margin=4_900))()
        assert decision.volume == pytest.approx(0.09)
        assert any("margin" in a for a in decision.adjustments)
        full = evaluate(account=make_account(margin=5_000))()
        assert "volume" in failed(full)

    def test_below_the_minimum_lot_is_a_rejection_not_a_minimum_trade(self) -> None:
        tiny = make_account(balance=100, equity=100)
        decision = evaluate(account=tiny, initial_balance=100, day_start_balance=100)()
        assert not decision.approved
        assert "volume" in failed(decision)
        assert "below the minimum lot" in decision.reasons[0]

    def test_max_volume_per_trade_and_daily_volume(self) -> None:
        assert evaluate(RiskLimits(max_volume_per_trade=0.3))().volume == pytest.approx(0.3)
        daily = RiskLimits(max_daily_volume=1.0)
        assert evaluate(daily, volume_today=0.85)().volume == pytest.approx(0.15)

    def test_sell_side_sizing_uses_the_bid(self) -> None:
        decision = evaluate()(side="SELL", stop_loss=1.10200, take_profit=1.09500)
        assert decision.approved
        assert decision.entry_price == BID
        assert decision.volume == pytest.approx(0.5)  # 100 / (20 points x 100,000 x 1e-5)

    def test_limit_order_sizes_from_its_entry(self) -> None:
        decision = evaluate()(
            order_type="LIMIT", entry_price=1.09900, stop_loss=1.09700, take_profit=1.10400
        )
        assert decision.approved, decision.reasons
        assert decision.entry_price == 1.09900
        assert decision.volume == pytest.approx(0.5)


# ============================================================================ gates
class TestGates:
    def test_kill_switch(self) -> None:
        state = KillSwitchState(mode=KillSwitchMode.HALT_NEW, reason="news", engaged_at=T0)
        decision = evaluate(kill_switch=state)()
        assert failed(decision) == {"kill_switch"}
        assert "news" in decision.reasons[0]
        assert evaluate()().approved

    def test_circuit_breakers(self) -> None:
        decision = evaluate(tripped_breakers=["api_error_rate: 6 of the last 10 failed"])()
        assert failed(decision) == {"circuit_breakers"}

    def test_capital_preservation(self) -> None:
        assert failed(evaluate(capital_preservation=True)()) == {"capital_preservation"}

    def test_max_drawdown(self) -> None:
        # 50% hard stop: 20,000 initial and 10,000 equity is exactly at the limit.
        assert "max_drawdown" in failed(evaluate(initial_balance=20_000)())
        assert "max_drawdown" not in failed(evaluate(initial_balance=19_000)())

    def test_daily_loss_limit(self) -> None:
        assert failed(evaluate(day_start_balance=10_400)()) == {"daily_loss_limit"}  # -3.8%
        assert evaluate(day_start_balance=10_200)().approved  # -2%

    def test_daily_profit_target(self) -> None:
        limits = RiskLimits(daily_profit_target=0.02)
        assert failed(evaluate(limits, day_start_balance=9_700)()) == {"daily_profit_target"}
        assert evaluate(limits, day_start_balance=9_900)().approved

    def test_daily_trades_and_volume(self) -> None:
        limits = RiskLimits(max_daily_trades=3, max_daily_volume=1.0)
        assert failed(evaluate(limits, trades_today=3)()) == {"max_daily_trades"}
        assert failed(evaluate(limits, volume_today=1.0)()) == {"max_daily_volume"}
        assert evaluate(limits, trades_today=2, volume_today=0.5)().approved


class TestMandate:
    MANDATE = Mandate(
        approved_by="owner",
        valid_from=T0 - timedelta(days=1),
        valid_until=T0 + timedelta(days=30),
        symbols=frozenset({"EURUSD"}),
        max_volume_per_trade=0.2,
        max_risk_per_trade=0.005,
        account_login=1234,
    )

    def test_real_money_needs_a_mandate(self) -> None:
        real = make_account(kind=AccountKind.REAL)
        assert failed(evaluate(account=real)()) == {"mandate"}
        decision = evaluate(account=real, mandate=self.MANDATE)()
        assert decision.approved
        # 0.5% risk -> 0.238 lots, then the mandate's 0.2-lot cap
        assert decision.volume == pytest.approx(0.2)
        assert any("mandate" in a for a in decision.adjustments)

    def test_demo_needs_one_only_when_configured(self) -> None:
        assert evaluate()().approved
        strict = RiskLimits(require_mandate_for_demo=True)
        assert failed(evaluate(strict)()) == {"mandate"}

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"valid_until": T0}, "mandate valid"),
            ({"symbols": frozenset({"GBPUSD"})}, "does not allow EURUSD"),
            ({"account_login": 999}, "account 999"),
        ],
    )
    def test_invalid_mandates(self, change: dict[str, Any], message: str) -> None:
        mandate = Mandate.model_validate({**self.MANDATE.model_dump(), **change})
        decision = evaluate(account=make_account(kind=AccountKind.REAL), mandate=mandate)()
        assert failed(decision) == {"mandate"}
        assert message in decision.reasons[0]

    def test_mandate_validation(self) -> None:
        with pytest.raises(ValueError, match="valid_until"):
            Mandate.model_validate(
                {**self.MANDATE.model_dump(), "valid_until": T0 - timedelta(days=2)}
            )
        with pytest.raises(ValueError, match="at least one symbol"):
            Mandate.model_validate({**self.MANDATE.model_dump(), "symbols": frozenset()})
        with pytest.raises(ValueError):
            Mandate.model_validate({**self.MANDATE.model_dump(), "max_risk_per_trade": 0.05})

    def test_total_volume_cap(self) -> None:
        mandate = self.MANDATE.model_copy(update={"max_total_volume": 0.5})
        decision = evaluate(
            account=make_account(kind=AccountKind.REAL),
            mandate=mandate,
            positions=[position(volume=0.4)],
        )()
        assert decision.volume == pytest.approx(0.1)


# ============================================================================ the proposal
class TestProposalChecks:
    def test_symbol_must_match_the_market_data(self) -> None:
        assert "symbol" in failed(evaluate()(symbol="GBPUSD"))

    def test_trading_disabled(self) -> None:
        assert "trading_allowed" in failed(
            evaluate(spec=EURUSD.model_copy(update={"trade_allowed": False}))()
        )
        assert "trading_allowed" in failed(evaluate(account=make_account(trade_allowed=False))())

    def test_pending_order_needs_an_entry(self) -> None:
        assert "entry_price" in failed(evaluate()(order_type="LIMIT"))

    @pytest.mark.parametrize(
        ("side", "sl", "tp"),
        [("BUY", 1.1005, 1.1050), ("SELL", 1.0980, 1.0950)],
    )
    def test_stop_on_the_wrong_side(self, side: str, sl: float, tp: float) -> None:
        assert "stop_side" in failed(evaluate()(side=side, stop_loss=sl, take_profit=tp))

    def test_target_on_the_wrong_side(self) -> None:
        assert "take_profit_side" in failed(evaluate()(take_profit=1.0990))

    def test_reward_risk(self) -> None:
        assert "reward_risk" in failed(evaluate()(take_profit=1.10200))  # 1.9 vs 2.1
        assert evaluate()(take_profit=1.10250).approved  # 2.4 vs 2.1
        assert evaluate(RiskLimits(min_reward_risk=0))(take_profit=1.10200).approved

    def test_take_profit_requirement(self) -> None:
        assert evaluate()(take_profit=None).approved
        required = RiskLimits(require_take_profit=True)
        assert failed(evaluate(required)(take_profit=None)) == {"take_profit"}

    def test_stop_distance_in_atr(self) -> None:
        assert "stop_distance_atr" in failed(evaluate(atr=0.0100)())  # 0.21 ATR: noise
        assert "stop_distance_atr" in failed(evaluate(atr=0.0003)())  # 7 ATR: too far
        assert evaluate(atr=None)().approved  # no ATR, no check

    def test_spread_relative_to_the_stop(self) -> None:
        wide = Tick(symbol="EURUSD", time=T0, bid=BID, ask=BID + 0.0006)
        decision = evaluate(tick=wide)(stop_loss=1.09900, take_profit=1.11000)
        assert "spread_to_stop" in failed(decision)

    def test_spread_in_pips(self) -> None:
        limits = RiskLimits(max_spread_pips=0.5)
        assert failed(evaluate(limits)()) == {"spread"}  # 1.0 pip spread
        assert evaluate(RiskLimits(max_spread_pips=1.5))().approved

    def test_signal_age(self) -> None:
        stale = evaluate(now=T0 + timedelta(minutes=16))()
        assert failed(stale) == {"signal_age"}
        assert evaluate(now=T0 + timedelta(minutes=14))().approved
        from_the_future = evaluate()(created_at=T0 + timedelta(hours=1))
        assert failed(from_the_future) == {"signal_age"}

    def test_price_grounding(self) -> None:
        invented = evaluate()(entry_price=1.09500)  # the model "saw" 50 pips away
        assert failed(invented) == {"price_grounding"}
        assert evaluate()(entry_price=1.10050).approved
        far_limit = evaluate()(
            order_type="LIMIT", entry_price=1.08000, stop_loss=1.07800, take_profit=1.08600
        )
        assert "price_grounding" in failed(far_limit)

    def test_news_blackout(self) -> None:
        def news(minutes: int, currency: str = "USD", impact: Impact = Impact.HIGH) -> Any:
            return [
                EconomicEvent(
                    time=T0 + timedelta(minutes=minutes),
                    currency=currency,
                    impact=impact,
                    title="NFP",
                )
            ]

        assert failed(evaluate(calendar=news(10))()) == {"news_blackout"}
        assert failed(evaluate(calendar=news(-20))()) == {"news_blackout"}
        assert evaluate(calendar=news(45))().approved
        assert evaluate(calendar=news(10, currency="JPY"))().approved
        assert evaluate(calendar=news(10, impact=Impact.MEDIUM))().approved
        assert evaluate(RiskLimits(news_blackout=timedelta(0)), calendar=news(0))().approved

    def test_currencies_from_the_symbol_name(self) -> None:
        bare = EURUSD.model_copy(update={"currency_base": "", "currency_profit": ""})
        ctx = make_context(spec=bare)
        assert ctx.symbol_currencies() == {"EUR", "USD"}

    def test_market_session(self) -> None:
        assert failed(evaluate(market_open=False)()) == {"market_open"}
        assert evaluate(market_open=True)().approved


# ============================================================================ portfolio
class TestPortfolio:
    LIMITS = RiskLimits(
        max_concurrent_positions=3,
        max_positions_per_symbol=1,
        max_same_direction=2,
        max_correlation=0.8,
    )

    def test_max_positions(self) -> None:
        three = [position("GBPUSD", Side.SELL), position("USDJPY", Side.SELL), position("AUDUSD")]
        assert "max_positions" in failed(evaluate(self.LIMITS, positions=three)())

    def test_per_symbol(self) -> None:
        assert failed(evaluate(self.LIMITS, positions=[position()])()) == {
            "max_positions_per_symbol"
        }

    def test_same_direction(self) -> None:
        two_longs = [position("AUDJPY"), position("NZDJPY")]
        assert failed(evaluate(self.LIMITS, positions=two_longs)()) == {"max_same_direction"}
        assert evaluate(self.LIMITS, positions=two_longs)(
            side="SELL", stop_loss=1.10200, take_profit=1.09500
        ).approved

    def test_correlation_blocks_stacking_but_not_hedging(self) -> None:
        def run(pos: Position, rho: float, side: str = "BUY") -> RiskDecision:
            stops = (
                {"stop_loss": 1.09800, "take_profit": 1.10500}
                if side == "BUY"
                else {"stop_loss": 1.10200, "take_profit": 1.09500}
            )
            return evaluate(self.LIMITS, positions=[pos], correlations={pos.symbol: rho})(
                side=side, **stops
            )

        assert failed(run(position("GBPUSD", Side.BUY), 0.9)) == {"correlation"}
        assert run(position("GBPUSD", Side.BUY), 0.9, side="SELL").approved  # hedge
        assert run(position("USDCHF", Side.BUY), -0.9).approved  # long USD vs short USD
        assert failed(run(position("USDCHF", Side.SELL), -0.9)) == {"correlation"}
        assert run(position("GBPUSD", Side.BUY), 0.5).approved  # weakly related


# ============================================================================ outputs
class TestDecision:
    def test_rejections_list_every_reason(self) -> None:
        decision = evaluate(day_start_balance=10_400, market_open=False)(stop_loss=None)
        assert failed(decision) == {"daily_loss_limit", "market_open", "stop_loss"}
        assert len(decision.reasons) == 3
        assert all(":" in reason for reason in decision.reasons)

    def test_to_order(self) -> None:
        decision = evaluate()()
        order = decision.to_order("maq-key-1", magic=7)
        assert (order.symbol, order.side, order.type) == ("EURUSD", Side.BUY, OrderType.MARKET)
        assert order.volume == decision.volume
        assert (order.price, order.sl, order.tp) == (ASK, 1.098, 1.105)
        assert order.magic == 7
        with pytest.raises(ValueError, match="rejected"):
            evaluate()(stop_loss=None).to_order("maq-key-2")

    def test_pending_order_keeps_its_expiry(self) -> None:
        decision = evaluate()(
            order_type="LIMIT",
            entry_price=1.09900,
            stop_loss=1.09700,
            take_profit=1.10400,
            expires_at=T0 + timedelta(hours=1),
        )
        order = decision.to_order("maq-key-3")
        assert order.type is OrderType.LIMIT
        assert order.expires_at == T0 + timedelta(hours=1)

    def test_audit_event_round_trip(self) -> None:
        proposal = make_proposal()
        ctx = make_context()
        decision = KERNEL.evaluate(proposal, ctx)
        event = RiskDecisionMade(
            ts=T0,
            proposal=proposal,
            decision=decision,
            equity=ctx.account.equity,
            drawdown=ctx.drawdown,
            daily_pnl_fraction=ctx.daily_pnl_fraction,
        )
        restored = parse_event(event.model_dump_json())
        assert restored == event

    def test_kernel_is_deterministic(self) -> None:
        assert evaluate()() == evaluate()()

    def test_unrelated_status_enum_is_untouched(self) -> None:
        # The kernel returns decisions, not order statuses.
        assert OrderStatus.NEW.value == "NEW"


class TestLimits:
    def test_from_shipped_config(self) -> None:
        config = load_config("config")
        limits = RiskLimits.from_config(config.risk, config.execution)
        assert limits.per_trade_risk == 0.01
        assert limits.daily_loss_limit == 0.03
        assert limits.max_drawdown == 0.5
        assert limits.drawdown_throttle == ((0.10, 0.75), (0.25, 0.5), (0.40, 0.25))
        assert limits.max_spread_pips == 3.0
        assert limits.signal_ttl == timedelta(minutes=15)
        assert limits.drawdown_multiplier(0.3) == 0.5
        assert limits.drawdown_multiplier(0.05) == 1.0

    @pytest.mark.parametrize(
        "change",
        [
            {"per_trade_risk": 0.03},
            {"per_trade_risk": 0},
            {"daily_loss_limit": 0.2},
            {"max_drawdown": 0.6},
            {"max_margin_usage": 1.5},
            {"min_confidence": 1.0},
            {"min_stop_atr": 7.0},
            {"min_reward_risk": -1},
            {"signal_ttl": timedelta(0)},
            {"drawdown_throttle": ((0.2, 0.5), (0.1, 0.75))},
            {"drawdown_throttle": ((0.1, 0.5), (0.2, 0.75))},
            {"drawdown_throttle": ((1.5, 0.5),)},
            {"per_trade_risk": float("nan")},
        ],
    )
    def test_cannot_be_looser_than_the_hard_limits_or_inconsistent(
        self, change: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError):
            RiskLimits(**change)

    def test_confidence_multiplier(self) -> None:
        limits = RiskLimits(min_confidence=0.6, min_confidence_multiplier=0.25)
        assert limits.confidence_multiplier(0.59) is None
        assert limits.confidence_multiplier(0.6) == pytest.approx(0.25)
        assert limits.confidence_multiplier(0.8) == pytest.approx(0.625)
        assert limits.confidence_multiplier(1.0) == pytest.approx(1.0)
        assert limits.confidence_multiplier(float("nan")) is None
