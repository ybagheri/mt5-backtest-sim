"""Saving and loading settings and runs."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mt5_backtest_sim.domain.enums import OperationType, OrderType, SimulationState
from mt5_backtest_sim.domain.models import Account, Operation, Order
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.persistence.settings_store import (
    Settings,
    load_settings,
    save_settings,
)
from mt5_backtest_sim.persistence.simulation_store import load_simulation, save_simulation
from mt5_backtest_sim.simulation.engine import EquityPoint, SimulationResult

BASE = datetime(2024, 1, 2, tzinfo=UTC)


def test_settings_round_trip(tmp_path: Path) -> None:
    settings = Settings(
        data_folder="/tmp/data",
        account_currency="EUR",
        atr_period=21,
        default_sl_distance=15,
        last_selected_symbols=["EURUSD", "GBPUSD"],
    )
    path = save_settings(settings, tmp_path / "simulator_settings.dat")

    assert path.is_file()
    restored = load_settings(path)
    assert restored == settings


def test_the_settings_file_is_gzip_json(tmp_path: Path) -> None:
    path = save_settings(Settings(), tmp_path / "simulator_settings.dat")
    with gzip.open(path, "rb") as handle:
        payload = json.loads(handle.read().decode("utf-8"))
    assert payload["account_currency"] == "USD"
    assert payload["atr_period"] == 14


def test_a_missing_settings_file_yields_the_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.dat") == Settings()


def _order() -> Order:
    return Order(
        ticket=7,
        symbol="EURUSD",
        order_type=OrderType.BUY,
        volume=Volume.from_lots("0.25"),
        open_time=BASE,
        open_price=Decimal("1.10000"),
        close_time=BASE + timedelta(minutes=5),
        close_price=Decimal("1.10100"),
        profit_loss=Money(Decimal("25.00")),
        commission=Money(Decimal("1.75")),
        close_operation_type=OperationType.TAKE_PROFIT,
        comment="[tp 1.10100]",
    )


def _result() -> SimulationResult:
    account = Account(
        initial_balance=Money(Decimal("10000")), balance=Money(Decimal("10025"))
    )
    order = _order()
    operation = Operation(
        time=BASE,
        symbol="EURUSD",
        operation_type=OperationType.OPEN_MARKET,
        order_type=OrderType.BUY,
        price=Decimal("1.10000"),
        volume=Volume.from_lots("0.25"),
        ticket=7,
        balance=Money(Decimal("10000")),
        profit=Money.zero(),
        commission=Money(Decimal("1.75")),
    )
    return SimulationResult(
        state=SimulationState.STOPPED,
        account=account,
        operations=(operation,),
        closed_orders=(order,),
        equity_curve=(
            EquityPoint(time=BASE, balance=Decimal("10000"), equity=Decimal("10000")),
            EquityPoint(time=BASE, balance=Decimal("10025"), equity=Decimal("10025")),
        ),
        starting_time=BASE,
        ending_time=BASE,
    )


def test_a_run_round_trips(tmp_path: Path) -> None:
    result = _result()
    path = save_simulation(result, tmp_path / "run.sim")

    restored = load_simulation(path)
    assert restored.state == "STOPPED"
    assert restored.account.balance == Money(Decimal("10025"))
    assert len(restored.operations) == 1
    assert len(restored.closed_orders) == 1
    order = restored.closed_orders[0]
    assert order.ticket == 7
    assert order.volume == Volume.from_lots("0.25")
    assert order.profit_loss == Money(Decimal("25.00"))
    assert order.close_operation_type is OperationType.TAKE_PROFIT
    assert len(restored.equity_curve) == 2


def test_a_run_file_is_gzip_json(tmp_path: Path) -> None:
    path = save_simulation(_result(), tmp_path / "run.sim")
    with gzip.open(path, "rb") as handle:
        document = json.loads(handle.read().decode("utf-8"))
    assert document["schema"] == 1
    assert document["account"]["balance"] == "10025"


def test_a_missing_run_file_is_reported(tmp_path: Path) -> None:
    from mt5_backtest_sim.domain.exceptions import SimulationError

    with pytest.raises(SimulationError):
        load_simulation(tmp_path / "absent.sim")
