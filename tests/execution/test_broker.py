"""Order lifecycle semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mt5_backtest_sim.domain.enums import OperationType, OrderType
from mt5_backtest_sim.domain.exceptions import InvalidOrderError, MarginError, PriceError
from mt5_backtest_sim.domain.models import Account, SymbolState, Tick
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.execution.broker import SimulatedBroker
from tests.conftest import BASE_TIME


def make_broker(
    state: SymbolState,
    balance: str = "10000",
    commission: str = "0",
) -> SimulatedBroker:
    account = Account(
        initial_balance=Money(Decimal(balance)),
        balance=Money(Decimal(balance)),
        commission_per_lot=Decimal(commission),
    )
    return SimulatedBroker(account, {state.spec.name: state})


def quote(second: int, bid: str, ask: str) -> Tick:
    from datetime import timedelta

    return Tick(
        time=BASE_TIME + timedelta(seconds=second),
        symbol="EURUSD",
        bid=Decimal(bid),
        ask=Decimal(ask),
    )


def test_market_buy_fills_at_the_ask(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    order = broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("0.10"))

    assert order.open_price == Decimal("1.10010")
    assert order.is_long
    assert order.is_market
    assert len(broker.market_orders) == 1


def test_market_sell_fills_at_the_bid(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    order = broker.open_market_order("EURUSD", OrderType.SELL, Volume.from_lots("0.10"))

    assert order.open_price == Decimal("1.10000")
    assert not order.is_long


def test_stop_loss_below_the_market_is_rejected(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    with pytest.raises(PriceError):
        broker.open_market_order(
            "EURUSD",
            OrderType.BUY,
            Volume.from_lots("0.10"),
            stop_loss=Decimal("1.10100"),
        )


def test_take_profit_above_the_market_is_rejected(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    with pytest.raises(PriceError):
        broker.open_market_order(
            "EURUSD",
            OrderType.BUY,
            Volume.from_lots("0.10"),
            take_profit=Decimal("1.09000"),
        )


def test_buy_limit_fills_when_the_ask_reaches_the_price(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    order = broker.place_pending_order(
        "EURUSD", OrderType.BUY_LIMIT, Decimal("1.09000"), Volume.from_lots("0.10")
    )
    assert order.is_pending
    assert len(broker.pending_orders) == 1

    broker.on_tick(quote(1, "1.08990", "1.09000"))

    assert not broker.pending_orders
    assert len(broker.market_orders) == 1
    assert broker.market_orders[0].open_price == Decimal("1.09000")
    assert broker.ledger.of_type(OperationType.EXECUTE_PENDING)


def test_buy_stop_fills_when_the_ask_rises_through_the_price(
    eurusd_state: SymbolState,
) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    broker.place_pending_order(
        "EURUSD", OrderType.BUY_STOP, Decimal("1.10100"), Volume.from_lots("0.10")
    )

    broker.on_tick(quote(1, "1.10100", "1.10110"))

    assert not broker.pending_orders
    assert len(broker.market_orders) == 1


def test_stop_loss_closes_the_position_at_the_stop_price(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    broker.open_market_order(
        "EURUSD",
        OrderType.BUY,
        Volume.from_lots("0.10"),
        stop_loss=Decimal("1.09500"),
    )

    broker.on_tick(quote(1, "1.09490", "1.09500"))

    assert not broker.market_orders
    closed = broker.closed_orders[0]
    assert closed.close_price == Decimal("1.09500")
    assert closed.close_operation_type is OperationType.STOP_LOSS
    # 510 points below the 1.10010 entry on 0.10 lot is -51.00 USD.
    assert closed.profit_loss == Money(Decimal("-51.00"))
    assert broker.account.balance == Money(Decimal("9949.00"))


def test_take_profit_closes_the_position_at_the_target_price(
    eurusd_state: SymbolState,
) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    broker.open_market_order(
        "EURUSD",
        OrderType.BUY,
        Volume.from_lots("0.10"),
        take_profit=Decimal("1.10500"),
    )

    broker.on_tick(quote(1, "1.10500", "1.10510"))

    closed = broker.closed_orders[0]
    assert closed.close_price == Decimal("1.10500")
    assert closed.close_operation_type is OperationType.TAKE_PROFIT
    assert closed.profit_loss == Money(Decimal("49.00"))


def test_oco_cancels_the_sibling_when_one_triggers(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    broker.place_pending_order(
        "EURUSD",
        OrderType.BUY_LIMIT,
        Decimal("1.09000"),
        Volume.from_lots("0.10"),
        is_oco=True,
    )
    broker.place_pending_order(
        "EURUSD",
        OrderType.SELL_STOP,
        Decimal("1.09000"),
        Volume.from_lots("0.10"),
        is_oco=True,
    )

    broker.on_tick(quote(1, "1.08990", "1.09000"))

    assert not broker.pending_orders
    assert len(broker.market_orders) == 1
    assert broker.ledger.of_type(OperationType.DELETE_PENDING_OCO)


def test_partial_close_splits_the_position(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    order = broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("0.20"))

    closed, remaining = broker.close_partially(order.ticket, Volume.from_lots("0.10"))

    assert closed.volume == Volume.from_lots("0.10")
    assert closed.from_ticket == order.ticket
    assert remaining.volume == Volume.from_lots("0.10")
    assert remaining.ticket == order.ticket
    assert len(broker.market_orders) == 1


def test_commission_is_charged_on_the_balance(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state, commission="5")
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    order = broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("0.10"))

    assert order.commission == Money(Decimal("0.50"))

    broker.on_tick(quote(1, "1.10100", "1.10110"))
    broker.close_market_order(order.ticket)

    # 90 points of profit on 0.10 lot is 9.00 USD, less 0.50 commission.
    assert broker.account.balance == Money(Decimal("10008.50"))


def test_pending_order_without_margin_is_deleted(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state, balance="100")
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    broker.place_pending_order(
        "EURUSD", OrderType.BUY_LIMIT, Decimal("1.09000"), Volume.from_lots("1.00")
    )

    broker.on_tick(quote(1, "1.08990", "1.09000"))

    assert not broker.pending_orders
    assert not broker.market_orders
    assert broker.ledger.of_type(OperationType.DELETE_PENDING_NO_MARGIN)


def test_market_order_without_margin_is_rejected(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state, balance="100")
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    with pytest.raises(MarginError):
        broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("1.00"))


def test_break_even_moves_the_stop_to_the_entry(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    order = broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("0.10"))
    order.break_even_at_points = 50
    order.break_even_move_to_points = 0

    broker.on_tick(quote(1, "1.10060", "1.10070"))

    assert order.stop_loss == Decimal("1.10010")


def test_trailing_stop_follows_the_price_up(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))
    order = broker.open_market_order("EURUSD", OrderType.BUY, Volume.from_lots("0.10"))
    order.trailing_stop_points = 100
    order.trailing_start_points = 50

    broker.on_tick(quote(1, "1.10100", "1.10110"))

    assert order.stop_loss == Decimal("1.10000")


def test_pending_order_price_on_the_wrong_side_is_rejected(
    eurusd_state: SymbolState,
) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    with pytest.raises(PriceError):
        broker.place_pending_order(
            "EURUSD", OrderType.BUY_LIMIT, Decimal("1.11000"), Volume.from_lots("0.10")
        )


def test_a_market_order_cannot_be_placed_as_pending(eurusd_state: SymbolState) -> None:
    broker = make_broker(eurusd_state)
    broker.on_tick(quote(0, "1.10000", "1.10010"))

    with pytest.raises(InvalidOrderError):
        broker.place_pending_order(
            "EURUSD", OrderType.BUY, Decimal("1.09000"), Volume.from_lots("0.10")
        )
