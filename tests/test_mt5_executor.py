"""execute_signal against the fake MT5 terminal: request building, sizing, filling, outcomes."""

from __future__ import annotations

from typing import Any

import pytest

from execution import mt5_executor
from execution.mt5_executor import execute_signal
from tests.fakes.mt5 import (
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    POSITION_TYPE_SELL,
    SYMBOL_FILLING_FOK,
    SYMBOL_FILLING_IOC,
    TRADE_ACTION_DEAL,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_NO_MONEY,
    FakeMT5,
)


def exec_cfg(**overrides: Any) -> dict[str, Any]:
    """The shipped safety defaults, live (dry_run False) unless overridden."""
    cfg: dict[str, Any] = {
        "enabled": True,
        "dry_run": False,
        "use_signal_volume": False,
        "use_risk_sizing": True,
        "default_volume": 0.01,
        "magic": 20250121,
        "comment": "multi-agent-quant",
        "deviation": 20,
        "max_spread_pips": 3.0,
        "require_tick_bid_ask": True,
        "enforce_safety": True,
        "position_management": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


def buy_signal(**overrides: Any) -> dict[str, Any]:
    signal: dict[str, Any] = {
        "symbol": "EURUSD",
        "action": "BUY",
        "volume": 5.0,  # what an LLM might ask for; risk sizing must win
        "entry": {"type": "market", "price": None},
        "risk": {
            "stop_loss_pips": 20,
            "take_profit_pips": 40,
            "risk_fraction_of_initial": 0.01,
        },
    }
    signal.update(overrides)
    return signal


@pytest.fixture
def mt5(fake_mt5: FakeMT5) -> FakeMT5:
    fake_mt5.add_symbol("EURUSD", bid=1.08500, ask=1.08510)
    return fake_mt5


def test_live_buy_builds_risk_sized_order(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])

    assert result.status == "submitted", result
    request = mt5.sent_requests[0]
    assert request["action"] == TRADE_ACTION_DEAL
    assert request["type"] == ORDER_TYPE_BUY
    assert request["price"] == pytest.approx(1.08510)  # buys fill at the ask
    assert request["sl"] == pytest.approx(1.08510 - 0.0020)
    assert request["tp"] == pytest.approx(1.08510 + 0.0040)
    # $10,000 * 1% = $100 risk over 20 pips at $10/pip/lot -> 0.5 lots, not the 5 lots asked for
    assert request["volume"] == pytest.approx(0.5)
    assert request["magic"] == 20250121
    assert mt5.initialized is False  # connection is always shut down


def test_sell_with_absolute_prices_uses_bid(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    signal = buy_signal(action="SELL", sl_price=1.0870, tp_price=1.0810)
    signal["risk"] = {"stop_loss_pips": 0, "take_profit_pips": 0, "risk_fraction_of_initial": 0.01}

    result = execute_signal(signal, exec_cfg(), mt5_cfg, positions=[])

    assert result.status == "submitted", result
    request = mt5.sent_requests[0]
    assert request["type"] == ORDER_TYPE_SELL
    assert request["price"] == pytest.approx(1.08500)
    assert (request["sl"], request["tp"]) == (1.0870, 1.0810)
    # stop distance 20 pips from the bid -> same $100 budget -> 0.5 lots
    assert request["volume"] == pytest.approx(0.5)
    assert "stop_distance_from_sl_price" in result.notes


def test_llm_volume_can_only_shrink_the_order(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    result = execute_signal(buy_signal(volume=0.2), exec_cfg(), mt5_cfg, positions=[])
    assert mt5.sent_requests[0]["volume"] == pytest.approx(0.2)
    assert "capped_by_requested_volume" in result.notes


def test_dry_run_never_sends(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    result = execute_signal(buy_signal(), exec_cfg(dry_run=True), mt5_cfg, positions=[])
    assert result.status == "dry_run"
    assert result.order_request["volume"] == pytest.approx(0.5)
    assert mt5.sent_requests == []


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC, ORDER_FILLING_IOC),
        (SYMBOL_FILLING_FOK, ORDER_FILLING_FOK),  # the old code sent IOC (enum 1) here
        (SYMBOL_FILLING_IOC, ORDER_FILLING_IOC),
        (0, ORDER_FILLING_RETURN),
    ],
)
def test_filling_type_follows_allowed_modes(
    mt5: FakeMT5, mt5_cfg: dict[str, Any], flags: int, expected: int
) -> None:
    mt5.symbols["EURUSD"]["filling_mode"] = flags
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "submitted"
    assert mt5.sent_requests[0]["type_filling"] == expected


def test_filling_types_are_always_valid_enums() -> None:
    valid = {ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN}
    for flags in range(8):
        types = mt5_executor._order_filling_types({"filling_mode": flags})
        assert set(types) == valid
        assert len(types) == 3


def test_retries_another_filling_mode_when_rejected(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.order_send_results.extend(
        [
            mt5.result(TRADE_RETCODE_INVALID_FILL, comment="Unsupported filling mode"),
            mt5.result(TRADE_RETCODE_DONE, volume=0.5),
        ]
    )
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])

    assert result.status == "submitted"
    first, second = mt5.sent_requests
    assert first["type_filling"] == ORDER_FILLING_IOC
    assert second["type_filling"] == ORDER_FILLING_FOK
    assert result.order_request["type_filling"] == ORDER_FILLING_FOK
    assert result.notes[-1] == f"fill_mode_retry:{ORDER_FILLING_FOK}"


def test_broker_rejection_is_reported(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.order_send_results.append(mt5.result(TRADE_RETCODE_NO_MONEY, comment="No money"))
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "rejected"
    assert result.errors == [f"order_rejected:{TRADE_RETCODE_NO_MONEY}:No money"]


def test_send_failure_reports_last_error(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.default_order_result = lambda request: None
    mt5.error = (-10004, "No IPC connection")
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "error"
    assert result.errors == ["order_send_failed:-10004:No IPC connection"]


def test_wide_spread_blocks_entry(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.symbols["EURUSD"]["spread"] = 45  # 4.5 pips on a 5-digit symbol
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "blocked"
    assert result.errors == ["spread_exceeds_config:4.50pips"]
    assert mt5.sent_requests == []


def test_untradeable_symbol_is_blocked(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.symbols["EURUSD"]["trade_mode"] = 0  # disabled
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "blocked"
    assert "symbol_not_tradeable:0" in result.errors


def test_invalid_signal_is_not_sent(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    signal = buy_signal()
    signal["risk"] = {"stop_loss_pips": 0, "take_profit_pips": 40}
    result = execute_signal(signal, exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "invalid"
    assert result.errors == ["invalid_stop_loss"]
    assert mt5.sent_requests == []


def test_hold_without_positions_is_skipped(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    result = execute_signal(buy_signal(action="HOLD"), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "skipped"
    assert mt5.sent_requests == []


def test_missing_terminal_path(mt5: FakeMT5) -> None:
    result = execute_signal(buy_signal(), exec_cfg(), {}, positions=[])
    assert result.status == "error"
    assert result.errors == ["missing_terminal_path"]


def test_missing_account_info(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.account = None
    mt5.error = (-1, "terminal not connected")
    result = execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert result.status == "error"
    assert result.errors == ["account_info_failed:-1:terminal not connected"]


def test_signal_volume_mode_uses_llm_volume(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    cfg = exec_cfg(use_signal_volume=True)
    execute_signal(buy_signal(volume=0.37), cfg, mt5_cfg, positions=[])
    assert mt5.sent_requests[0]["volume"] == pytest.approx(0.37)


def test_volume_is_clamped_to_symbol_limits(mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
    mt5.symbols["EURUSD"].update(volume_max=0.3, volume_step=0.1)
    execute_signal(buy_signal(), exec_cfg(), mt5_cfg, positions=[])
    assert mt5.sent_requests[0]["volume"] == pytest.approx(0.3)


class TestPositionManagement:
    @staticmethod
    def cfg(**pm: Any) -> dict[str, Any]:
        management = {
            "enabled": True,
            "close_on_opposite": True,
            "allow_reverse": False,
            "allow_scale_in": False,
            "update_stops_on_same_direction": True,
            "update_stops_on_hold": False,
            "sl_tp_reference": "current",
        }
        management.update(pm)
        return exec_cfg(position_management=management)

    def test_opposite_position_is_closed_and_entry_blocked(
        self, mt5: FakeMT5, mt5_cfg: dict[str, Any]
    ) -> None:
        position = mt5.add_position("EURUSD", type=POSITION_TYPE_SELL, volume=0.3, ticket=77)
        result = execute_signal(buy_signal(), self.cfg(), mt5_cfg, positions=[position])

        assert result.status == "managed"
        (close,) = mt5.sent_requests
        assert close["position"] == 77
        assert close["type"] == ORDER_TYPE_BUY  # buying back the short
        assert close["volume"] == pytest.approx(0.3)
        assert result.management[0]["action"] == "close"

    def test_reverse_closes_then_enters(self, mt5: FakeMT5, mt5_cfg: dict[str, Any]) -> None:
        position = mt5.add_position("EURUSD", type=POSITION_TYPE_SELL, ticket=78)
        result = execute_signal(
            buy_signal(), self.cfg(allow_reverse=True), mt5_cfg, positions=[position]
        )
        assert result.status == "submitted"
        close, entry = mt5.sent_requests
        assert close["position"] == 78
        assert "position" not in entry

    def test_dry_run_records_management_without_sending(
        self, mt5: FakeMT5, mt5_cfg: dict[str, Any]
    ) -> None:
        position = mt5.add_position("EURUSD", type=POSITION_TYPE_SELL)
        cfg = self.cfg()
        cfg["dry_run"] = True
        result = execute_signal(buy_signal(), cfg, mt5_cfg, positions=[position])
        assert result.management[0]["status"] == "dry_run"
        assert mt5.sent_requests == []
