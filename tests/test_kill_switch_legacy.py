"""The kill switch in the legacy loop: halt skips cycles, flatten closes our positions once."""

from __future__ import annotations

import importlib.util
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from execution.mt5_executor import flatten_positions
from maq_risk.kill_switch import ENV_VAR, KillSwitch
from maq_risk.models import KillSwitchMode
from tests.fakes.mt5 import FakeMT5

REPO_ROOT = Path(__file__).resolve().parents[1]
MAGIC = 20250121
T0 = datetime(2026, 11, 3, 12, tzinfo=UTC)
CFG = {"magic": MAGIC, "deviation": 20, "comment": "multi-agent-quant"}


@pytest.fixture(scope="module")
def run_loop() -> Any:
    spec = importlib.util.spec_from_file_location("run_loop", REPO_ROOT / "scripts" / "run_loop.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def market(fake_mt5: FakeMT5) -> FakeMT5:
    fake_mt5.add_symbol("EURUSD", bid=1.1000, ask=1.1001)
    fake_mt5.add_symbol("USDJPY", bid=150.00, ask=150.02, digits=3, point=0.001)
    fake_mt5.add_position("EURUSD", type=0, volume=0.3, ticket=1, magic=MAGIC)
    fake_mt5.add_position("USDJPY", type=1, volume=0.2, ticket=2, magic=MAGIC)
    fake_mt5.add_position("EURUSD", type=0, volume=1.0, ticket=3, magic=0)  # opened by hand
    return fake_mt5


def positions(fake: FakeMT5) -> list[dict[str, Any]]:
    return [dict(p) for p in fake.positions]


class TestFlattenPositions:
    def test_closes_only_our_positions(self, market: FakeMT5) -> None:
        results = flatten_positions(positions(market), CFG, live=True)
        assert [(r["position"], r["status"]) for r in results] == [(1, "closed"), (2, "closed")]
        eur, jpy = market.sent_requests
        assert (eur["position"], eur["type"], eur["volume"]) == (1, market.ORDER_TYPE_SELL, 0.3)
        assert eur["price"] == 1.1000  # a long closes at the bid
        assert (jpy["position"], jpy["type"], jpy["price"]) == (2, market.ORDER_TYPE_BUY, 150.02)
        assert eur["type_filling"] == market.ORDER_FILLING_IOC

    def test_dry_run_sends_nothing(self, market: FakeMT5) -> None:
        results = flatten_positions(positions(market), CFG, live=False)
        assert [r["status"] for r in results] == ["dry_run", "dry_run"]
        assert market.sent_requests == []

    def test_magic_zero_means_everything(self, market: FakeMT5) -> None:
        results = flatten_positions(positions(market), {**CFG, "magic": 0}, live=False)
        assert len(results) == 3

    def test_failures_are_reported(self, market: FakeMT5) -> None:
        market.order_send_results.extend([None, market.result(10019)])
        market.error = (-2, "IPC timeout")
        results = flatten_positions(positions(market), CFG, live=True)
        assert results[0]["status"] == "error"
        assert "IPC timeout" in results[0]["error"]
        assert results[1]["status"] == "rejected"
        assert len(market.sent_requests) == 2  # no answer is never resent

    def test_unknown_symbol(self, market: FakeMT5) -> None:
        orphan = {"ticket": 9, "symbol": "XAUUSD", "type": 0, "volume": 0.1, "magic": MAGIC}
        [result] = flatten_positions([orphan], CFG, live=True)
        assert result["status"] == "error"


class TestRunLoopHooks:
    def test_kill_switch_file_follows_the_environment(
        self, run_loop: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert run_loop.kill_switch_for(tmp_path).path == tmp_path / "logs" / "kill_switch.json"
        monkeypatch.setenv(ENV_VAR, str(tmp_path / "elsewhere.json"))
        assert run_loop.kill_switch_for(tmp_path).path == tmp_path / "elsewhere.json"

    def test_not_engaged(self, run_loop: Any, tmp_path: Path) -> None:
        switch = KillSwitch(tmp_path / "kill.json")
        state, flattened = run_loop.enforce_kill_switch(
            switch, None, mt5_cfg={}, execution_cfg=CFG, live=True, logger=logging.getLogger()
        )
        assert (state, flattened) == (None, None)

    def test_halt_does_not_touch_positions(
        self, run_loop: Any, tmp_path: Path, market: FakeMT5, mt5_cfg: dict[str, Any]
    ) -> None:
        switch = KillSwitch(tmp_path / "kill.json")
        switch.engage(KillSwitchMode.HALT_NEW, "checking something", now=T0)
        state, flattened = run_loop.enforce_kill_switch(
            switch, None, mt5_cfg=mt5_cfg, execution_cfg=CFG, live=True, logger=logging.getLogger()
        )
        assert state is not None and state.mode is KillSwitchMode.HALT_NEW
        assert flattened is None
        assert market.sent_requests == []

    def test_flatten_runs_once_per_engagement(
        self, run_loop: Any, tmp_path: Path, market: FakeMT5, mt5_cfg: dict[str, Any]
    ) -> None:
        switch = KillSwitch(tmp_path / "kill.json")
        switch.engage(KillSwitchMode.FLATTEN, "flash crash", now=T0)
        kwargs = {"mt5_cfg": mt5_cfg, "execution_cfg": CFG, "live": True,
                  "logger": logging.getLogger()}  # fmt: skip
        state, flattened = run_loop.enforce_kill_switch(switch, None, **kwargs)
        assert state is not None and state.mode is KillSwitchMode.FLATTEN
        assert flattened == T0
        assert len(market.sent_requests) == 2
        _, flattened = run_loop.enforce_kill_switch(switch, flattened, **kwargs)
        assert len(market.sent_requests) == 2  # not again for the same engagement
        assert not market.initialized  # connection closed again
