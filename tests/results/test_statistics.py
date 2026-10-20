"""Run statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mt5_backtest_sim.domain.models import Account, Order
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.results.statistics import compute_statistics
from mt5_backtest_sim.simulation.engine import EquityPoint

BASE = datetime(2024, 1, 2, tzinfo=UTC)


def make_order(
    ticket: int,
    profit: str,
    index: int = 0,
    is_long: bool = True,
    commission: str = "0",
) -> Order:
    from mt5_backtest_sim.domain.enums import OrderType

    order_type = OrderType.BUY if is_long else OrderType.SELL
    return Order(
        ticket=ticket,
        symbol="EURUSD",
        order_type=order_type,
        volume=Volume.from_lots("0.10"),
        open_time=BASE + timedelta(minutes=index),
        open_price=Decimal("1.10000"),
        close_time=BASE + timedelta(minutes=index + 1),
        close_price=Decimal("1.10000"),
        profit_loss=Money(Decimal(profit)),
        commission=Money(Decimal(commission)),
    )


def make_account() -> Account:
    return Account(
        initial_balance=Money(Decimal("10000")), balance=Money(Decimal("10000"))
    )


def test_gross_and_net_profit_are_separated() -> None:
    orders = [make_order(1, "100"), make_order(2, "-40"), make_order(3, "60")]
    stats = compute_statistics(orders, make_account())

    assert stats.total_trades == 3
    assert stats.win_trades == 2
    assert stats.loss_trades == 1
    assert stats.gross_profit == Money(Decimal("160"))
    assert stats.gross_loss == Money(Decimal("40"))
    assert stats.net_profit == Money(Decimal("120"))


def test_profit_factor_is_none_without_losses() -> None:
    stats = compute_statistics([make_order(1, "100")], make_account())
    assert stats.profit_factor is None


def test_profit_factor_is_gross_profit_over_gross_loss() -> None:
    stats = compute_statistics(
        [make_order(1, "100"), make_order(2, "-50")], make_account()
    )
    assert stats.profit_factor == Decimal(2)


def test_consecutive_wins_are_counted() -> None:
    orders = [
        make_order(1, "10", 0),
        make_order(2, "20", 1),
        make_order(3, "30", 2),
        make_order(4, "-5", 3),
    ]
    stats = compute_statistics(orders, make_account())
    assert stats.max_consecutive_wins == 3
    assert stats.max_consecutive_wins_amount == Money(Decimal("60"))


def test_commission_is_totalled() -> None:
    orders = [make_order(1, "10", commission="1.5"), make_order(2, "20", 1, commission="1.5")]
    stats = compute_statistics(orders, make_account())
    assert stats.total_commission == Money(Decimal("3.00"))


def test_drawdown_is_measured_from_the_equity_curve() -> None:
    curve = [
        EquityPoint(time=BASE, balance=Decimal("10000"), equity=Decimal("10000")),
        EquityPoint(time=BASE, balance=Decimal("10000"), equity=Decimal("10000")),
        EquityPoint(time=BASE, balance=Decimal("10000"), equity=Decimal("9000")),
        EquityPoint(time=BASE, balance=Decimal("10000"), equity=Decimal("9500")),
    ]
    stats = compute_statistics([], make_account(), curve)
    assert stats.max_money_drawdown == Money(Decimal("1000"))
    assert stats.absolute_drawdown == Money(Decimal("1000"))


def test_an_empty_run_produces_zeroes() -> None:
    stats = compute_statistics([], make_account())
    assert stats.total_trades == 0
    assert stats.net_profit == Money.zero()
    assert stats.profit_factor is None
