"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mt5_backtest_sim.domain.enums import SpreadType
from mt5_backtest_sim.domain.models import SymbolSpec, SymbolState, Tick
from mt5_backtest_sim.domain.value_objects import Money, Volume

EURUSD = SymbolSpec(
    name="EURUSD",
    base_currency="EUR",
    quote_currency="USD",
    digits=5,
    point=Decimal("0.00001"),
    contract_size=Decimal("100000"),
    min_volume=Volume(Decimal("0.01")),
    max_volume=Volume(Decimal("100")),
    volume_step=Decimal("0.01"),
    stop_level_points=0,
    spread_type=SpreadType.REAL,
    pip_points=10,
)

BASE_TIME = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


@pytest.fixture
def eurusd_spec() -> SymbolSpec:
    """A five-digit EURUSD contract specification."""
    return EURUSD


@pytest.fixture
def eurusd_state(eurusd_spec: SymbolSpec) -> SymbolState:
    """A live EURUSD symbol state."""
    return SymbolState(spec=eurusd_spec)


@pytest.fixture
def account() -> object:
    """A fresh account with 10,000 USD."""
    from mt5_backtest_sim.domain.models import Account

    return Account(initial_balance=Money(Decimal("10000")), balance=Money(Decimal("10000")))


def tick(
    second: int = 0,
    bid: str = "1.10000",
    ask: str = "1.10010",
    symbol: str = "EURUSD",
) -> Tick:
    """Build one tick relative to :data:`BASE_TIME`."""
    return Tick(
        time=BASE_TIME + __import__("datetime").timedelta(seconds=second),
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
    )
