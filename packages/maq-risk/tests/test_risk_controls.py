"""Circuit breakers, the kill switch and the legacy-signal adapter."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from maq_core.config.models import BreakerSettings
from maq_core.domain import OrderType, Side, Tick
from maq_risk import (
    BudgetBreaker,
    CircuitBreakers,
    KillSwitch,
    KillSwitchMode,
    RateBreaker,
    RiskKernel,
    RiskLimits,
    proposal_from_signal,
)
from maq_risk.kill_switch import ENV_VAR, default_path
from maq_risk.testing import ASK, BID, EURUSD, T0, make_context


class TestBreakers:
    def test_api_error_rate_trips_and_cools_down(self) -> None:
        breakers = CircuitBreakers(
            BreakerSettings(window=10, min_samples=4, api_error_rate=0.5, cooldown_minutes=30)
        )
        for ok in (True, False, True):
            breakers.record_api_call(ok, T0)
        assert breakers.tripped(T0) == []  # three samples: not enough to judge
        breakers.record_api_call(False, T0)  # 2 of 4 failed
        [status] = breakers.tripped(T0)
        assert status.startswith("api_error_rate: 2 of the last 4 failed")
        assert breakers.tripped(T0 + timedelta(minutes=29)) != []
        assert breakers.tripped(T0 + timedelta(minutes=30)) == []

    def test_window_restarts_after_the_cooldown(self) -> None:
        breaker = RateBreaker(
            "schema", threshold=0.5, window=4, min_samples=2, cooldown=timedelta(minutes=5)
        )
        breaker.record(False, T0)
        breaker.record(False, T0)
        assert breaker.status(T0) is not None
        breaker.record(False, T0 + timedelta(minutes=1))  # ignored while cooling down
        later = T0 + timedelta(minutes=6)
        breaker.record(True, later)
        breaker.record(True, later)
        assert breaker.status(later) is None

    def test_schema_failures(self) -> None:
        breakers = CircuitBreakers(BreakerSettings(min_samples=2, schema_failure_rate=0.3))
        breakers.record_schema_check(True, T0)
        breakers.record_schema_check(False, T0)
        assert [s.split(":")[0] for s in breakers.tripped(T0)] == ["schema_failure_rate"]

    def test_llm_budget_resets_each_day(self) -> None:
        breakers = CircuitBreakers(BreakerSettings(llm_daily_budget_usd=5.0))
        breakers.record_llm_cost(3.0, T0)
        assert breakers.tripped(T0) == []
        breakers.record_llm_cost(2.5, T0 + timedelta(minutes=5))
        assert breakers.tripped(T0) == ["llm_daily_budget_usd: spent 5.50 of 5.00 today"]
        assert breakers.tripped(T0 + timedelta(days=1)) == []
        assert CircuitBreakers().llm_budget is None
        CircuitBreakers().record_llm_cost(100, T0)  # no budget: nothing to trip

    def test_tripped_breakers_block_the_kernel(self) -> None:
        breakers = CircuitBreakers(BreakerSettings(min_samples=1, api_error_rate=1.0))
        breakers.record_api_call(False, T0)
        ctx = make_context(tripped_breakers=breakers.tripped(T0))
        from maq_risk.testing import make_proposal

        decision = RiskKernel(RiskLimits()).evaluate(make_proposal(), ctx)
        assert [c.name for c in decision.failed_checks] == ["circuit_breakers"]

    def test_validation(self) -> None:
        with pytest.raises(ValueError):
            RateBreaker("x", threshold=0, window=5, min_samples=1, cooldown=timedelta(1))
        with pytest.raises(ValueError):
            RateBreaker("x", threshold=0.5, window=5, min_samples=6, cooldown=timedelta(1))
        with pytest.raises(ValueError):
            BudgetBreaker("x", 0)


class TestKillSwitch:
    def test_engage_status_release(self, tmp_path: Path) -> None:
        switch = KillSwitch(tmp_path / "state" / "kill.json")
        assert switch.state() is None
        assert not switch.engaged
        state = switch.engage(KillSwitchMode.FLATTEN, "flash crash", by="telegram", now=T0)
        assert switch.state() == state
        assert switch.engaged
        # Another process sees the same file.
        assert KillSwitch(switch.path).state() == state
        assert switch.release() is True
        assert switch.release() is False
        assert switch.state() is None

    def test_unreadable_file_counts_as_engaged(self, tmp_path: Path) -> None:
        path = tmp_path / "kill.json"
        path.write_text("{not json", encoding="utf-8")
        state = KillSwitch(path).state()
        assert state is not None
        assert state.mode is KillSwitchMode.HALT_NEW
        assert "not a valid kill switch file" in state.reason

    def test_default_reason_and_atomic_write(self, tmp_path: Path) -> None:
        switch = KillSwitch(tmp_path / "kill.json")
        assert switch.engage().reason == "manual stop"
        assert [p.name for p in tmp_path.iterdir()] == ["kill.json"]  # no temp leftovers

    def test_path_from_the_environment(self, tmp_path: Path) -> None:
        assert default_path({}) == Path("logs") / "kill_switch.json"
        assert default_path({ENV_VAR: str(tmp_path / "k.json")}) == tmp_path / "k.json"


class TestSignalAdapter:
    TICK = Tick(symbol="EURUSD", time=T0, bid=BID, ask=ASK)

    def adapt(self, **signal: object) -> object:
        base: dict[str, object] = {
            "model": "deepseek",
            "symbol": "EURUSD",
            "action": "BUY",
            "volume": 0.3,
            "confidence": 0.8,
            "entry": {"type": "market", "price": None},
            "risk": {"stop_loss_pips": 20, "take_profit_pips": 40},
            "analysis": {"setup": "pullback"},
            "ttl_minutes": 60,
        }
        base.update(signal)
        return proposal_from_signal(base, tick=self.TICK, spec=EURUSD, created_at=T0)

    def test_market_signal_in_pips(self) -> None:
        proposal = self.adapt()
        assert proposal is not None
        assert proposal.side is Side.BUY
        assert proposal.order_type is OrderType.MARKET
        assert proposal.stop_loss == pytest.approx(ASK - 0.0020)
        assert proposal.take_profit == pytest.approx(ASK + 0.0040)
        assert proposal.requested_volume == 0.3
        assert proposal.created_at == T0
        assert proposal.source == "deepseek"
        assert proposal.rationale == "pullback"
        assert proposal.expires_at is None

    def test_sell_and_absolute_prices_win(self) -> None:
        proposal = self.adapt(action="SELL", sl_price=1.1030, tp_price=1.0950)
        assert proposal is not None
        assert (proposal.stop_loss, proposal.take_profit) == (1.1030, 1.0950)

    def test_limit_signal_measures_from_its_entry(self) -> None:
        proposal = self.adapt(entry={"type": "limit", "price": 1.0990})
        assert proposal is not None
        assert proposal.order_type is OrderType.LIMIT
        assert proposal.entry_price == 1.0990
        assert proposal.stop_loss == pytest.approx(1.0970)
        assert proposal.expires_at == T0 + timedelta(minutes=60)

    def test_hold_and_garbage(self) -> None:
        assert self.adapt(action="HOLD") is None
        assert self.adapt(action=None) is None
        junk = self.adapt(
            volume="lots", confidence=float("nan"), risk={"stop_loss_pips": -5}, entry="x"
        )
        assert junk is not None
        assert junk.stop_loss is None
        assert junk.requested_volume is None
        assert junk.confidence == 0.0

    def test_adapted_signal_goes_through_the_kernel(self) -> None:
        proposal = self.adapt()
        assert proposal is not None
        decision = RiskKernel(RiskLimits()).evaluate(proposal, make_context())
        assert decision.approved, decision.reasons
        assert decision.volume == pytest.approx(0.3)  # the model asked for less than the budget


class TestKillSwitchFailures:
    def test_unreadable_path_counts_as_engaged(self, tmp_path: Path) -> None:
        directory = tmp_path / "kill.json"
        directory.mkdir()  # reading a directory raises an OSError
        state = KillSwitch(directory).state()
        assert state is not None
        assert "cannot read" in state.reason

    def test_failed_write_leaves_no_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken_replace(src: str, dst: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("maq_risk.kill_switch.os.replace", broken_replace)
        with pytest.raises(OSError, match="disk full"):
            KillSwitch(tmp_path / "kill.json").engage()
        assert list(tmp_path.iterdir()) == []
