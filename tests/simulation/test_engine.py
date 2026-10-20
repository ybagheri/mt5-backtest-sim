"""The simulation engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mt5_backtest_sim.domain.enums import OrderType, SimulationState
from mt5_backtest_sim.domain.models import Account, SymbolSpec, SymbolState, Tick
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.execution.broker import SimulatedBroker
from mt5_backtest_sim.market_data.feeder import TickFeeder
from mt5_backtest_sim.simulation.engine import SimulationEngine
from mt5_backtest_sim.simulation.settings import SimulationSettings
from mt5_backtest_sim.strategy.base import StrategyContext

BASE = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


class ListSource:
    """A tick source over a fixed list of ticks."""

    def __init__(self, ticks: list[Tick], symbol: str = "EURUSD") -> None:
        self._ticks = ticks
        self._index = 0
        self.symbol = symbol

    @property
    def start_time(self) -> datetime:
        return self._ticks[0].time

    @property
    def end_time(self) -> datetime:
        return self._ticks[-1].time

    def next_tick(self) -> Tick | None:
        if self._index >= len(self._ticks):
            return None
        tick = self._ticks[self._index]
        self._index += 1
        return tick

    def rewind_to(self, moment: datetime) -> None:
        self._index = 0
        while self._index < len(self._ticks) and self._ticks[self._index].time < moment:
            self._index += 1


def make_ticks(count: int = 60) -> list[Tick]:
    ticks: list[Tick] = []
    for index in range(count):
        price = Decimal("1.10000") + Decimal(index) * Decimal("0.00010")
        ticks.append(
            Tick(
                time=BASE + timedelta(seconds=index),
                symbol="EURUSD",
                bid=price,
                ask=price + Decimal("0.00010"),
            )
        )
    return ticks


def build(ticks: list[Tick]) -> tuple[SimulationEngine, SimulatedBroker, SymbolState]:
    spec = SymbolSpec(
        name="EURUSD",
        base_currency="EUR",
        quote_currency="USD",
        digits=5,
        point=Decimal("0.00001"),
        contract_size=Decimal("100000"),
    )
    state = SymbolState(spec=spec)
    account = Account(
        initial_balance=Money(Decimal("10000")), balance=Money(Decimal("10000"))
    )
    broker = SimulatedBroker(account, {"EURUSD": state})
    engine = SimulationEngine(broker, TickFeeder([ListSource(ticks)]), SimulationSettings())
    return engine, broker, state


def test_running_out_of_ticks_ends_the_run() -> None:
    engine, _, _ = build(make_ticks(10))
    result = engine.run_all()
    assert result.state is SimulationState.NO_MORE_TICKS


def test_a_time_window_stops_the_run() -> None:
    ticks = make_ticks(100)
    engine, _, _ = build(ticks)
    result = engine.run_all(until_time=ticks[20].time)
    assert result.state is SimulationState.STOPPED
    assert result.ending_time is not None
    assert result.ending_time <= ticks[20].time


def test_a_strategy_sees_every_tick() -> None:
    ticks = make_ticks(25)
    engine, _, _ = build(ticks)
    seen: list[Tick] = []
    engine.subscribe(seen.append)
    engine.run_all()
    assert len(seen) == len(ticks)


def test_a_strategy_can_open_and_close_positions() -> None:
    ticks = make_ticks(10)
    engine, broker, _ = build(ticks)

    class Alternating:
        """Enter on the first tick, leave on the second, and repeat."""

        def on_tick(self, tick: Tick, context: StrategyContext) -> None:
            if not context.open_positions():
                context.broker.open_market_order(
                    tick.symbol, OrderType.BUY, Volume.from_lots("0.10")
                )
            else:
                context.broker.close_market_order(context.open_positions()[0].ticket)

    engine.attach_strategy(Alternating())
    engine.run_all()

    assert broker.closed_orders
    # Every entry is matched by an exit, so the ledger holds two operations per trade.
    assert len(broker.ledger) == 2 * len(broker.closed_orders)


def test_the_equity_curve_is_sampled_not_per_tick() -> None:
    # One hundred ticks per second: the curve keeps one point per second.
    ticks = [
        Tick(
            time=BASE + timedelta(milliseconds=10 * index),
            symbol="EURUSD",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
        )
        for index in range(500)
    ]
    engine, _, _ = build(ticks)
    engine.run_all()
    assert 0 < len(engine.equity_curve) < len(ticks)


def test_rewind_restores_the_ledger() -> None:
    ticks = make_ticks(20)
    engine, broker, _ = build(ticks)

    def strategy(tick: Tick, context: StrategyContext) -> None:
        context.broker.open_market_order(
            tick.symbol, OrderType.BUY, Volume.from_lots("0.10")
        )

    class OpenEach:
        """Open a position on every tick."""

        def on_tick(self, tick: Tick, context: StrategyContext) -> None:
            strategy(tick, context)

    engine.attach_strategy(OpenEach())
    engine.run_all()
    operations_before = len(broker.ledger)

    engine.rewind(ticks[5].time)

    assert len(broker.ledger) <= operations_before
    assert not broker.market_orders
    assert broker.account.balance == Money(Decimal("10000"))


def test_a_bankrupt_account_stops_the_run() -> None:
    ticks = make_ticks(10)
    engine, broker, _ = build(ticks)
    broker.account.balance = Money(Decimal("0"))
    result = engine.run_all()
    assert result.state is SimulationState.BANKRUPT
