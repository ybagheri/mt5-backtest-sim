"""Profile the full engine stack."""

from __future__ import annotations

import cProfile
import pstats
import sys
from datetime import UTC, datetime
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from examples.ma_crossover import MovingAverageCrossover

from mt5_backtest_sim.domain.models import Account, SymbolSpec, SymbolState
from mt5_backtest_sim.domain.value_objects import Money
from mt5_backtest_sim.execution.broker import SimulatedBroker
from mt5_backtest_sim.market_data.ctf import CtfTickFile
from mt5_backtest_sim.market_data.feeder import TickFeeder
from mt5_backtest_sim.simulation.engine import SimulationEngine
from mt5_backtest_sim.simulation.settings import SimulationSettings
from mt5_backtest_sim.strategy.base import StrategyContext

PATH = r"C:\Users\BazikadeStore\Documents\ForexSimulator_Data\sim_ticks_EURUSD.ctf"
LIMIT = 1_000_000


def run() -> None:
    spec = SymbolSpec(
        name="EURUSD",
        base_currency="EUR",
        quote_currency="USD",
        digits=5,
        point=Decimal("0.00001"),
        contract_size=Decimal("100000"),
    )
    state = SymbolState(spec=spec)
    reader = CtfTickFile(PATH, symbol="EURUSD")
    reader.seek_to(datetime(2022, 1, 3, tzinfo=UTC))
    feeder = TickFeeder([reader])
    account = Account(
        initial_balance=Money(Decimal("10000")),
        balance=Money(Decimal("10000")),
        commission_per_lot=Decimal("3.5"),
    )
    broker = SimulatedBroker(account, {"EURUSD": state})
    engine = SimulationEngine(broker, feeder, SimulationSettings())
    strategy = MovingAverageCrossover()
    context = StrategyContext(broker)
    engine.subscribe(lambda tick: strategy.on_tick(tick, context))

    processed = 0
    while True:
        tick = feeder.next_tick()
        if tick is None:
            break
        processed += 1
        if processed > LIMIT:
            break
        engine._apply_tick(tick)
    print("ticks:", processed)


profiler = cProfile.Profile()
profiler.enable()
run()
profiler.disable()
pstats.Stats(profiler).sort_stats("tottime").print_stats(18)
