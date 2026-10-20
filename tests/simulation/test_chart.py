"""Bar aggregation from ticks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mt5_backtest_sim.domain.enums import Timeframe
from mt5_backtest_sim.domain.models import Tick
from mt5_backtest_sim.simulation.chart import BarAggregator, Chart, bar_start

BASE = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


def tick(second: int, bid: str = "1.10000", ask: str | None = None) -> Tick:
    price = Decimal(bid)
    return Tick(
        time=BASE + timedelta(seconds=second),
        symbol="EURUSD",
        bid=price,
        ask=Decimal(ask) if ask is not None else price + Decimal("0.00010"),
    )


def test_the_first_tick_opens_a_bar() -> None:
    aggregator = BarAggregator()
    assert aggregator.update(tick(0)) is True
    assert len(aggregator.candles) == 1
    assert aggregator.candles[0].open_time == BASE
    assert aggregator.candles[0].open == Decimal("1.10000")


def test_ticks_inside_the_bar_update_it() -> None:
    aggregator = BarAggregator()
    aggregator.update(tick(0))
    assert aggregator.update(tick(30, bid="1.10100")) is False

    bar = aggregator.candles[0]
    assert bar.close == Decimal("1.10100")
    assert bar.high == Decimal("1.10100")
    assert bar.low == Decimal("1.10000")
    assert bar.tick_volume == 2


def test_a_new_minute_opens_a_new_bar() -> None:
    aggregator = BarAggregator()
    aggregator.update(tick(0))
    assert aggregator.update(tick(60)) is True
    assert len(aggregator.candles) == 2
    assert aggregator.candles[1].open_time == BASE + timedelta(minutes=1)


def test_bars_align_to_the_minute() -> None:
    assert bar_start(BASE + timedelta(seconds=37), 60) == BASE


def test_higher_timeframes_group_the_ticks() -> None:
    aggregator = BarAggregator(timeframe=Timeframe.H1)
    aggregator.update(tick(0))
    aggregator.update(tick(1800, bid="1.10500"))
    assert len(aggregator.candles) == 1
    assert aggregator.candles[0].high == Decimal("1.10500")
    assert aggregator.candles[0].tick_volume == 2


def test_rewind_clears_the_bars() -> None:
    aggregator = BarAggregator()
    aggregator.update(tick(0))
    aggregator.rewind()
    assert aggregator.candles == ()
    assert aggregator.dirty is False


def test_clear_except_last_keeps_the_forming_bar() -> None:
    aggregator = BarAggregator()
    aggregator.update(tick(0))
    aggregator.update(tick(60))
    aggregator.clear_except_last()
    assert len(aggregator.candles) == 1
    assert aggregator.candles[0].open_time == BASE + timedelta(minutes=1)


def test_the_chart_is_dirty_until_it_is_flushed() -> None:
    from mt5_backtest_sim.domain.models import SymbolSpec, SymbolState

    spec = SymbolSpec(
        name="EURUSD",
        base_currency="EUR",
        quote_currency="USD",
        digits=5,
        point=Decimal("0.00001"),
        contract_size=Decimal("100000"),
    )
    chart = Chart(SymbolState(spec=spec))
    chart.update(tick(0))
    assert chart.dirty is True
    chart.flush()
    assert chart.dirty is False
