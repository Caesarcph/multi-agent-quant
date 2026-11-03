"""Properties the kernel must keep for any input (P3-09).

Run the long version with ``HYPOTHESIS_PROFILE=thorough`` (100,000 examples).
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import assume, event, given
from hypothesis import strategies as st

from maq_core.config.limits import HARD_LIMITS
from maq_core.domain import AccountKind, AccountState, Side, SymbolSpec, Tick
from maq_risk import (
    KillSwitchMode,
    KillSwitchState,
    RiskContext,
    RiskKernel,
    RiskLimits,
    TradeProposal,
)
from maq_risk.testing import T0


@dataclass
class Scenario:
    limits: RiskLimits
    proposal: TradeProposal
    context: RiskContext
    entry: float
    stop_distance: float


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    digits = draw(st.sampled_from([2, 3, 5]))
    point = 10.0**-digits
    step = draw(st.sampled_from([0.01, 0.1, 1.0]))
    volume_min = step * draw(st.integers(1, 5))
    spec = SymbolSpec(
        symbol="TEST",
        digits=digits,
        point=point,
        tick_size=point,
        tick_value=draw(st.floats(0.01, 50)),
        contract_size=draw(st.sampled_from([1.0, 100.0, 1_000.0, 100_000.0])),
        volume_min=volume_min,
        volume_max=draw(st.floats(volume_min, 1_000)),
        volume_step=step,
    )
    bid = round(draw(st.floats(0.5, 5_000)), digits)
    ask = round(bid + draw(st.integers(0, 50)) * point, digits)
    assume(bid > 0)
    side = draw(st.sampled_from(list(Side)))
    entry = ask if side is Side.BUY else bid
    stop = round(entry - side.sign * draw(st.integers(1, 5_000)) * point, digits)
    assume(stop > 0 and side.sign * (entry - stop) > 0)
    take = draw(
        st.none()
        | st.integers(1, 10_000).map(lambda n: round(entry + side.sign * n * point, digits))
    )
    assume(take is None or take > 0)

    balance = draw(st.floats(100, 1e7))
    equity = balance * draw(st.floats(0.7, 1.5))
    margin = equity * draw(st.floats(0, 0.3))
    account = AccountState(
        kind=AccountKind.DEMO,
        currency="USD",
        balance=balance,
        equity=equity,
        margin=margin,
        margin_free=equity - margin,
        leverage=draw(st.sampled_from([1, 30, 100, 500])),
    )
    limits = RiskLimits(
        per_trade_risk=draw(st.floats(0.0001, HARD_LIMITS.max_per_trade_risk)),
        commission_per_lot=draw(st.floats(0, 20)),
        max_margin_usage=draw(st.floats(0.3, 1.0)),
        max_volume_per_trade=draw(st.none() | st.floats(0.01, 100)),
        drawdown_throttle=((0.1, 0.75), (0.25, 0.5)),
        # Switch off checks unrelated to sizing so approvals are common.
        min_reward_risk=0.0,
        max_spread_to_stop=1.0,
        max_price_deviation_pips=1e9,
    )
    proposal = TradeProposal(
        symbol="TEST",
        side=side,
        stop_loss=stop,
        take_profit=take,
        confidence=draw(st.floats(0.4, 1)),
        created_at=T0,
        requested_volume=draw(st.none() | st.floats(0.001, 10_000)),
    )
    context = RiskContext(
        now=T0,
        account=account,
        spec=spec,
        tick=Tick(symbol="TEST", time=T0, bid=bid, ask=ask),
        initial_balance=balance * draw(st.floats(0.8, 1.2)),
        day_start_balance=equity * draw(st.floats(0.995, 1.02)),
    )
    return Scenario(limits, proposal, context, entry, abs(entry - stop))


@given(scenarios())
def test_risk_never_exceeds_the_budget(s: Scenario) -> None:
    decision = RiskKernel(s.limits).evaluate(s.proposal, s.context)
    event("approved" if decision.approved else "rejected")
    if not decision.approved:
        assert decision.volume == 0 and decision.risk_amount == 0
        return

    account, spec = s.context.account, s.context.spec
    capital = min(account.balance, account.equity)
    loss_per_lot = s.stop_distance * spec.value_per_price_unit() + s.limits.commission_per_lot
    risk = decision.volume * loss_per_lot
    assert risk <= capital * s.limits.per_trade_risk * (1 + 1e-9)
    assert decision.risk_amount <= capital * HARD_LIMITS.max_per_trade_risk * (1 + 1e-9)
    assert decision.stop_loss is not None


@given(scenarios())
def test_volume_is_always_tradable_and_within_every_cap(s: Scenario) -> None:
    decision = RiskKernel(s.limits).evaluate(s.proposal, s.context)
    if not decision.approved:
        return
    account, spec = s.context.account, s.context.spec
    assert spec.is_valid_volume(decision.volume)
    if s.proposal.requested_volume is not None:
        assert decision.volume <= s.proposal.requested_volume + 1e-9
    if s.limits.max_volume_per_trade is not None:
        assert decision.volume <= s.limits.max_volume_per_trade + 1e-9
    margin_after = account.margin + spec.margin(decision.volume, s.entry, account.leverage)
    assert margin_after <= s.limits.max_margin_usage * account.equity * (1 + 1e-9) + 1e-9


@given(scenarios())
def test_no_stop_or_an_engaged_kill_switch_never_trades(s: Scenario) -> None:
    kernel = RiskKernel(s.limits)
    no_stop = s.proposal.model_copy(update={"stop_loss": None})
    assert not kernel.evaluate(no_stop, s.context).approved
    halted = s.context.model_copy(
        update={
            "kill_switch": KillSwitchState(
                mode=KillSwitchMode.HALT_NEW, reason="test", engaged_at=T0
            )
        }
    )
    assert not kernel.evaluate(s.proposal, halted).approved
