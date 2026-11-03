"""maq_core.domain: specs, prices, volumes, orders."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from maq_core.domain import (
    CLIENT_ORDER_ID_MAX_LENGTH,
    AccountKind,
    AccountState,
    Bar,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
    SymbolSpec,
    Tick,
    Timeframe,
    floor_to_step,
    make_client_order_id,
)

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

EURUSD = SymbolSpec(
    symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
    currency_base="EUR", currency_profit="USD", currency_margin="EUR",
)  # fmt: skip
# On a USD account one USDJPY tick is worth 0.001 * 100_000 / 150 USD.
USDJPY = SymbolSpec(
    symbol="USDJPY", digits=3, point=0.001, tick_size=0.001, tick_value=100 / 150,
)  # fmt: skip
XAUUSD = SymbolSpec(
    symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, contract_size=100,
    volume_min=0.01, volume_max=50, volume_step=0.01,
)  # fmt: skip


def market(**overrides: object) -> OrderRequest:
    data: dict[str, object] = {
        "client_order_id": "maq-test-1",
        "symbol": "EURUSD",
        "side": Side.BUY,
        "volume": 0.1,
    }
    data.update(overrides)
    return OrderRequest.model_validate(data)


class TestSide:
    def test_sign_and_opposite(self) -> None:
        assert Side.BUY.sign == 1 and Side.SELL.sign == -1
        assert Side.BUY.opposite is Side.SELL and Side.SELL.opposite is Side.BUY

    def test_values_match_the_signal_schema(self) -> None:
        assert Side("BUY") is Side.BUY
        assert f"{Side.SELL}" == "SELL"


class TestSymbolSpec:
    def test_pip_size_follows_fx_convention(self) -> None:
        assert EURUSD.pip_size == pytest.approx(0.0001)
        assert USDJPY.pip_size == pytest.approx(0.01)
        assert XAUUSD.pip_size == pytest.approx(0.01)  # 2 digits: one point

    def test_pip_value_for_a_standard_lot(self) -> None:
        assert EURUSD.pip_value(1.0) == pytest.approx(10.0)
        assert USDJPY.pip_value(1.0) == pytest.approx(1000 / 150)
        assert EURUSD.pip_value(0.1) == pytest.approx(1.0)

    def test_profit(self) -> None:
        assert EURUSD.profit(Side.BUY, 1.0, 1.08000, 1.08100) == pytest.approx(100.0)
        assert EURUSD.profit(Side.SELL, 1.0, 1.08000, 1.08100) == pytest.approx(-100.0)
        assert XAUUSD.profit(Side.BUY, 1.0, 2000.0, 2010.0) == pytest.approx(1000.0)

    def test_notional_converts_through_tick_value(self) -> None:
        assert EURUSD.notional(1.0, 1.08) == pytest.approx(108_000)
        # USDJPY on a USD account: one lot is 100k USD whatever the price.
        assert USDJPY.notional(1.0, 150.0) == pytest.approx(100_000)
        assert XAUUSD.notional(1.0, 2000.0) == pytest.approx(200_000)

    def test_margin(self) -> None:
        assert EURUSD.margin(1.0, 1.08, leverage=100) == pytest.approx(1080.0)
        assert EURUSD.margin(1.0, 1.08, leverage=0) == pytest.approx(108_000)

    @pytest.mark.parametrize(
        ("volume", "expected"),
        [
            (0.3, 0.3),  # exact multiple stays put despite 0.3 / 0.01 float error
            (0.129, 0.12),  # floors, never rounds up
            (0.0099, 0.0),  # below the broker minimum: no trade, not volume_min
            (500.0, 100.0),  # capped at volume_max
            (0.0, 0.0),
            (-1.0, 0.0),
            (math.nan, 0.0),
            (math.inf, 0.0),
        ],
    )
    def test_floor_volume(self, volume: float, expected: float) -> None:
        assert EURUSD.floor_volume(volume) == pytest.approx(expected)

    def test_floor_volume_with_off_grid_maximum(self) -> None:
        spec = EURUSD.model_copy(update={"volume_max": 0.255, "volume_step": 0.01})
        assert spec.floor_volume(10) == pytest.approx(0.25)

    def test_is_valid_volume(self) -> None:
        assert EURUSD.is_valid_volume(0.01)
        assert EURUSD.is_valid_volume(0.3)
        assert not EURUSD.is_valid_volume(0.015)
        assert not EURUSD.is_valid_volume(0.001)
        assert not EURUSD.is_valid_volume(101)
        assert not EURUSD.is_valid_volume(math.nan)

    def test_normalize_price(self) -> None:
        assert EURUSD.normalize_price(1.0812345678) == 1.08123
        assert XAUUSD.normalize_price(2000.126) == 2000.13

    def test_rejects_inverted_volume_range(self) -> None:
        with pytest.raises(ValidationError, match="volume_min"):
            SymbolSpec.model_validate({**EURUSD.model_dump(), "volume_min": 1.0, "volume_max": 0.5})

    def test_is_immutable_and_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            EURUSD.symbol = "GBPUSD"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            SymbolSpec.model_validate({**EURUSD.model_dump(), "tick_value": math.nan})

    @given(
        volume=st.floats(min_value=0, max_value=1e6, allow_nan=False),
        step=st.sampled_from([0.01, 0.1, 1.0, 0.05]),
    )
    def test_floor_volume_is_tradable_and_never_larger(self, volume: float, step: float) -> None:
        spec = EURUSD.model_copy(update={"volume_step": step, "volume_min": step})
        lots = spec.floor_volume(volume)
        assert lots <= volume + 1e-9
        assert lots == 0.0 or spec.is_valid_volume(lots)


class TestFloorToStep:
    def test_rejects_bad_step(self) -> None:
        with pytest.raises(ValueError, match="step"):
            floor_to_step(1.0, 0)

    @given(st.floats(min_value=0, max_value=1e4, allow_nan=False))
    def test_result_is_a_multiple_not_above_value(self, value: float) -> None:
        result = floor_to_step(value, 0.01)
        assert result <= value + 1e-9
        assert value - result < 0.01 + 1e-9


class TestTickAndBar:
    def test_tick_prices(self) -> None:
        tick = Tick(symbol="EURUSD", time=T0, bid=1.08000, ask=1.08012)
        assert tick.spread == pytest.approx(0.00012)
        assert tick.mid == pytest.approx(1.08006)
        assert tick.entry_price(Side.BUY) == 1.08012
        assert tick.entry_price(Side.SELL) == 1.08000
        assert tick.exit_price(Side.BUY) == 1.08000
        assert tick.exit_price(Side.SELL) == 1.08012

    def test_crossed_tick_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="crossed"):
            Tick(symbol="EURUSD", time=T0, bid=1.1, ask=1.0)

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            Tick(symbol="EURUSD", time=datetime(2026, 11, 3), bid=1.0, ask=1.0)

    def test_timestamps_are_converted_to_utc(self) -> None:
        tokyo = timezone(timedelta(hours=9))
        tick = Tick(symbol="EURUSD", time=datetime(2026, 11, 3, 21, tzinfo=tokyo), bid=1, ask=1)
        assert tick.time == T0
        assert tick.time.tzinfo is UTC

    def test_inconsistent_bar_is_rejected(self) -> None:
        Bar(time=T0, open=1.0, high=1.2, low=0.9, close=1.1)
        with pytest.raises(ValidationError, match="inconsistent bar"):
            Bar(time=T0, open=1.0, high=1.05, low=0.9, close=1.1)

    def test_timeframe_duration(self) -> None:
        assert Timeframe.M15.duration == timedelta(minutes=15)
        assert Timeframe("H4").duration == timedelta(hours=4)


class TestOrderRequest:
    def test_market_order_defaults(self) -> None:
        order = market()
        assert order.type is OrderType.MARKET
        assert order.price is None

    @pytest.mark.parametrize("order_type", [OrderType.LIMIT, OrderType.STOP])
    def test_pending_orders_need_a_price(self, order_type: OrderType) -> None:
        with pytest.raises(ValidationError, match="needs a price"):
            market(type=order_type)

    def test_expiry_only_for_pending_orders(self) -> None:
        with pytest.raises(ValidationError, match="expires_at"):
            market(expires_at=T0)
        market(type=OrderType.LIMIT, price=1.07, expires_at=T0)

    @pytest.mark.parametrize(
        ("side", "price", "sl", "tp"),
        [
            (Side.BUY, 1.08, 1.09, None),  # BUY stop above the price
            (Side.BUY, 1.08, None, 1.07),  # BUY target below the price
            (Side.SELL, 1.08, 1.07, None),
            (Side.SELL, 1.08, None, 1.09),
            (Side.BUY, None, 1.09, 1.08),  # no price, but sl above tp on a BUY
        ],
    )
    def test_stops_on_the_wrong_side_are_rejected(
        self, side: Side, price: float | None, sl: float | None, tp: float | None
    ) -> None:
        with pytest.raises(ValidationError, match=r"side|sl"):
            market(side=side, price=price, sl=sl, tp=tp)

    def test_stops_on_the_right_side(self) -> None:
        market(side=Side.BUY, price=1.08, sl=1.07, tp=1.10)
        market(side=Side.SELL, price=1.08, sl=1.09, tp=1.06)

    @pytest.mark.parametrize("volume", [0.0, -0.1, math.nan, math.inf])
    def test_volume_must_be_positive_and_finite(self, volume: float) -> None:
        with pytest.raises(ValidationError):
            market(volume=volume)

    @pytest.mark.parametrize("key", ["", "has space", "x" * (CLIENT_ORDER_ID_MAX_LENGTH + 1)])
    def test_client_order_id_fits_an_mt5_comment(self, key: str) -> None:
        with pytest.raises(ValidationError):
            market(client_order_id=key)

    def test_json_round_trip(self) -> None:
        order = market(type=OrderType.LIMIT, price=1.07, sl=1.06, tp=1.09, expires_at=T0)
        assert OrderRequest.model_validate_json(order.model_dump_json()) == order


class TestClientOrderId:
    def test_deterministic_and_short(self) -> None:
        key = make_client_order_id("cycle-42", "EURUSD", Side.BUY)
        assert key == make_client_order_id("cycle-42", "EURUSD", "BUY")
        assert key != make_client_order_id("cycle-43", "EURUSD", "BUY")
        assert key.startswith("maq-")
        assert len(key) <= CLIENT_ORDER_ID_MAX_LENGTH
        market(client_order_id=key)  # valid as an order key

    def test_needs_parts_and_short_prefix(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            make_client_order_id()
        with pytest.raises(ValueError, match="too long"):
            make_client_order_id("x", prefix="p" * 20)


class TestOrderResult:
    def test_rejected_helper(self) -> None:
        result = OrderResult.rejected(
            market(), RejectReason.NO_MONEY, "not enough margin", time=T0, broker_code="10019"
        )
        assert result.is_rejected and not result.is_filled
        assert result.status.is_terminal
        assert result.filled_volume == 0.0

    def test_consistency_rules(self) -> None:
        base = {
            "client_order_id": "k",
            "symbol": "EURUSD",
            "side": "BUY",
            "requested_volume": 0.1,
            "time": T0,
        }
        with pytest.raises(ValidationError, match="reject_reason"):
            OrderResult.model_validate({**base, "status": "REJECTED"})
        with pytest.raises(ValidationError, match="only for rejected"):
            OrderResult.model_validate(
                {**base, "status": "FILLED", "reject_reason": "NO_MONEY", "filled_volume": 0}
            )
        with pytest.raises(ValidationError, match="exceeds"):
            OrderResult.model_validate(
                {**base, "status": "FILLED", "filled_volume": 0.2, "avg_fill_price": 1.0}
            )
        with pytest.raises(ValidationError, match="avg_fill_price"):
            OrderResult.model_validate({**base, "status": "FILLED", "filled_volume": 0.1})

    def test_terminal_statuses(self) -> None:
        assert {s for s in OrderStatus if s.is_terminal} == {
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }


class TestAccountState:
    def test_margin_level(self) -> None:
        account = AccountState(
            kind=AccountKind.DEMO, currency="USD", balance=1000, equity=900, margin=300,
            margin_free=600,
        )  # fmt: skip
        assert account.margin_level == pytest.approx(300.0)
        assert account.model_copy(update={"margin": 0}).margin_level is None
        assert not account.kind.is_real_money
        assert AccountKind.REAL.is_real_money
