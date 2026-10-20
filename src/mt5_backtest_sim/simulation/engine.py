"""The simulation engine.

The engine owns the clock. It pulls ticks from the feeder, hands them to the broker and
keeps the run state. It knows nothing about how a fill works (the broker does) and
nothing about where the ticks came from (the feeder does), which is what keeps both
testable on their own.

The engine is an ordinary object, not a singleton. Two engines can run side by side in
one process without sharing state, and a test can build one in three lines.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from mt5_backtest_sim.domain.enums import OperationType, SimulationState, Timeframe
from mt5_backtest_sim.domain.models import Account, Operation, Order, Tick
from mt5_backtest_sim.domain.value_objects import Money
from mt5_backtest_sim.execution.broker import SimulatedBroker
from mt5_backtest_sim.market_data.feeder import TickFeeder
from mt5_backtest_sim.simulation.chart import Chart
from mt5_backtest_sim.simulation.settings import SimulationSettings
from mt5_backtest_sim.strategy.base import StrategyContext

__all__ = ["EquityPoint", "SimulationEngine", "SimulationResult"]


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One point on the equity curve."""

    time: datetime
    balance: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Everything a finished run produced."""

    state: SimulationState
    account: Account
    operations: tuple[Operation, ...]
    closed_orders: tuple[Order, ...]
    equity_curve: tuple[EquityPoint, ...]
    starting_time: datetime | None
    ending_time: datetime | None

    @property
    def is_finished(self) -> bool:
        """Whether the run reached a terminal state."""
        return self.state is not SimulationState.RUNNING


class SimulationEngine:
    """Drives a broker over a tick stream."""

    def __init__(
        self,
        broker: SimulatedBroker,
        feeder: TickFeeder,
        settings: SimulationSettings,
        equity_sample_seconds: int = 1,
        max_equity_points: int = 200_000,
        timeframes: tuple[Timeframe, ...] = (Timeframe.M1,),
    ) -> None:
        self.broker = broker
        self.feeder = feeder
        self.settings = settings
        self.state = SimulationState.RUNNING
        self.current_time: datetime | None = None
        self.ticks_processed = 0
        self.equity_curve: list[EquityPoint] = []
        self._subscribers: list[Callable[[Tick], None]] = []
        self._pending_tick: Tick | None = None
        self._equity_sample_seconds = max(1, equity_sample_seconds)
        self._max_equity_points = max_equity_points
        self._equity_interval = self._equity_sample_seconds
        self._last_equity_second = -(1 << 62)
        self.timeframes = timeframes
        self.charts: dict[tuple[str, Timeframe], Chart] = {
            (name, timeframe): Chart(state, timeframe)
            for name, state in broker.symbols.items()
            for timeframe in timeframes
        }

    def chart(self, symbol: str, timeframe: Timeframe = Timeframe.M1) -> Chart:
        """The chart of one symbol in one timeframe."""
        return self.charts[(symbol, timeframe)]

    # ----------------------------------------------------------------- wiring

    def subscribe(self, callback: Callable[[Tick], None]) -> None:
        """Register a callback that sees every tick before the broker processes it."""
        self._subscribers.append(callback)

    def attach_strategy(self, strategy: Any) -> None:
        """Attach a strategy, handing it a context bound to this engine's broker."""
        context = StrategyContext(self.broker)
        self.subscribe(lambda tick: strategy.on_tick(tick, context))

    # ------------------------------------------------------------------- time

    def _next_tick(self) -> Tick | None:
        if self._pending_tick is not None:
            tick = self._pending_tick
            self._pending_tick = None
            return tick
        return self.feeder.next_tick()

    def _push_back(self, tick: Tick) -> None:
        self._pending_tick = tick

    # -------------------------------------------------------------------- run

    def run_forward(self, until_time: datetime) -> int:
        """Advance the simulation to ``until_time``.

        Returns the number of ticks processed. The run stops early when the account
        goes bankrupt or the tick stream ends.
        """
        if self.state is not SimulationState.RUNNING:
            return 0
        processed = 0
        while True:
            tick = self._next_tick()
            if tick is None:
                self.state = SimulationState.NO_MORE_TICKS
                break
            if tick.time > until_time:
                self._push_back(tick)
                # The window ended before the data did: the run was cut short on
                # purpose, which is a different outcome from running out of ticks.
                self.state = SimulationState.STOPPED
                break
            self._apply_tick(tick)
            processed += 1
            self.ticks_processed += 1
            if self.broker.account.is_bankrupt():
                self.state = SimulationState.BANKRUPT
                break
        return processed

    def run_all(
        self,
        until_time: datetime | None = None,
        close_at_end: bool = True,
    ) -> SimulationResult:
        """Run to the end of the tick stream and return the result."""
        self.run_forward(until_time if until_time is not None else self._far_future())
        if close_at_end and self.state is not SimulationState.BANKRUPT:
            self.broker.close_all_market_orders(time=self.current_time)
            self.broker.delete_all_pending_orders(time=self.current_time)
            self._record_equity()
        return self.result()

    def _far_future(self) -> datetime:
        """A timestamp beyond every tick a source can hold."""
        return datetime.max.replace(tzinfo=UTC)

    def _apply_tick(self, tick: Tick) -> None:
        # The quote is recorded first so that a strategy reacting to this tick fills at
        # this tick's price, then the strategy acts, then the broker resolves resting
        # orders and stop levels against the same quote.
        self.broker.apply_quote(tick)
        for timeframe in self.timeframes:
            self.charts[(tick.symbol, timeframe)].update(tick)
        for callback in self._subscribers:
            callback(tick)
        self.broker.process(tick)
        self.current_time = tick.time
        self._record_equity()

    def _record_equity(self) -> None:
        """Append a point to the equity curve.

        A tick-level curve is useless in practice: a year of EURUSD is hundreds of
        millions of ticks, and the resulting curve would cost gigabytes to hold and
        minutes to build. The curve is therefore sampled on the simulation clock, and
        thinned further if it still grows past the point limit.
        """
        if self.current_time is None:
            return
        second = int(self.current_time.timestamp())
        if second - self._last_equity_second < self._equity_interval:
            return
        account = self.broker.account
        self.equity_curve.append(
            EquityPoint(
                time=self.current_time,
                balance=account.balance.value,
                equity=account.equity.value,
            )
        )
        self._last_equity_second = second
        if len(self.equity_curve) > self._max_equity_points:
            self.equity_curve = self.equity_curve[::2]
            self._equity_interval *= 2
            self._last_equity_second = second

    # ----------------------------------------------------------------- rewind

    def rewind(self, moment: datetime) -> None:
        """Jump the simulation back to ``moment``.

        The order book, the ledger and the tick stream are all rewound together. The
        account balance is restored from the balance recorded on the last operation at
        or before ``moment``, so the run continues from the state it had then rather
        than from today's state.
        """
        self.feeder.rewind_to(moment)
        self._pending_tick = None
        self.broker.market_orders.clear()
        self.broker.pending_orders.clear()
        self.broker.closed_orders.clear()
        self.broker.ledger.truncate_after(moment)
        balance = self.broker.account.initial_balance.value
        for operation in self.broker.ledger:
            balance = operation.balance.value
        self.broker.account.balance = Money(balance)
        self.broker.account.set_floating(Money(Decimal(0)), Money(Decimal(0)))
        for state in self.broker.symbols.values():
            state.rewind()
        self.current_time = moment
        self.state = SimulationState.RUNNING
        self.equity_curve = [p for p in self.equity_curve if p.time <= moment]

    # ---------------------------------------------------------------- result

    def result(self) -> SimulationResult:
        """The current result of the run."""
        return SimulationResult(
            state=self.state,
            account=self.broker.account,
            operations=self.broker.ledger.operations,
            closed_orders=tuple(self.broker.closed_orders),
            equity_curve=tuple(self.equity_curve),
            starting_time=self.settings.starting_time,
            ending_time=self.current_time,
        )

    def operations_of(self, *types: OperationType) -> tuple[Operation, ...]:
        """Every recorded operation of the given types."""
        return self.broker.ledger.of_type(*types)
