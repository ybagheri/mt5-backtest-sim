"""Bar aggregation.

Bars are built from ticks the way the reference simulator builds them: the bid price
is the reference, the bar opens at the first tick of its period, and the tick count is
carried as the volume. A bar is only complete once a tick of the next period arrives,
so a strategy never sees a partially formed bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from mt5_backtest_sim.domain.enums import Timeframe
from mt5_backtest_sim.domain.models import Candle, SymbolState, Tick

__all__ = ["BarAggregator", "Chart"]


def bar_start(moment: datetime, timeframe_seconds: int) -> datetime:
    """The opening time of the bar that contains ``moment``."""
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % timeframe_seconds, tz=moment.tzinfo)


@dataclass(slots=True)
class BarAggregator:
    """Builds bars of one timeframe from a tick stream."""

    timeframe: Timeframe = Timeframe.M1
    _candles: list[Candle] = field(default_factory=list)
    _dirty: bool = True

    @property
    def candles(self) -> tuple[Candle, ...]:
        """The bars built so far, oldest first."""
        return tuple(self._candles)

    @property
    def dirty(self) -> bool:
        """Whether the last bar changed since the last flush."""
        return self._dirty

    def update(self, tick: Tick) -> bool:
        """Add a tick to the chart.

        Returns ``True`` when a new bar was opened.
        """
        seconds = self.timeframe.seconds
        opened = bar_start(tick.time, seconds)
        if not self._candles or tick.time - self._candles[-1].open_time >= timedelta(
            seconds=seconds
        ):
            self._candles.append(
                Candle(
                    open_time=opened,
                    timeframe_seconds=seconds,
                    open=tick.bid,
                    high=tick.bid,
                    low=tick.bid,
                    close=tick.bid,
                    volume=Decimal(1),
                    tick_volume=1,
                )
            )
            self._dirty = True
            return True

        current = self._candles[-1]
        closed = Candle(
            open_time=current.open_time,
            timeframe_seconds=current.timeframe_seconds,
            open=current.open,
            high=max(current.high, tick.bid),
            low=min(current.low, tick.bid),
            close=tick.bid,
            volume=current.volume + 1,
            tick_volume=current.tick_volume + 1,
        )
        self._candles[-1] = closed
        self._dirty = True
        return False

    def rewind(self) -> None:
        """Drop every bar, used when the engine jumps back in time."""
        self._dirty = False
        self._candles.clear()

    def clear_except_last(self) -> None:
        """Keep only the bar that is still forming."""
        self._dirty = False
        if len(self._candles) > 1:
            last = self._candles[-1]
            self._candles.clear()
            self._candles.append(last)

    def flush(self) -> None:
        """Mark the chart as clean after the bars were sent onwards."""
        self._dirty = False


class Chart:
    """The chart of one symbol.

    A thin, stateful wrapper around :class:`BarAggregator` that also knows the symbol
    the bars belong to.
    """

    def __init__(
        self,
        symbol: SymbolState,
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self.symbol = symbol
        self.aggregator = BarAggregator(timeframe=timeframe)

    @property
    def candles(self) -> tuple[Candle, ...]:
        """The bars built so far."""
        return self.aggregator.candles

    @property
    def dirty(self) -> bool:
        """Whether the chart changed since the last flush."""
        return self.aggregator.dirty

    def update(self, tick: Tick) -> bool:
        """Add a tick to the chart."""
        return self.aggregator.update(tick)

    def rewind(self) -> None:
        """Drop every bar."""
        self.aggregator.rewind()

    def clear_except_last(self) -> None:
        """Keep only the bar that is still forming."""
        self.aggregator.clear_except_last()

    def flush(self) -> None:
        """Mark the chart as clean."""
        self.aggregator.flush()
