"""Multi-symbol tick feeder.

The feeder merges several :class:`~mt5_backtest_sim.market_data.interfaces.TickSource`
streams into one chronological stream. Ties — two symbols quoting at the same instant —
are broken by symbol name, so the order of events is fully determined by the data and
never by the order in which the sources happened to be registered. That determinism is
what makes a run reproducible.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from datetime import datetime

from mt5_backtest_sim.domain.models import Tick
from mt5_backtest_sim.market_data.interfaces import TickSource

__all__ = ["TickFeeder"]


class TickFeeder:
    """Merge several tick sources into one chronological stream."""

    def __init__(self, sources: list[TickSource]) -> None:
        if not sources:
            raise ValueError("a feeder needs at least one tick source")
        self._sources = list(sources)
        self._heap: list[tuple[datetime, str, int, Tick]] = []
        self._exhausted = False
        self._push_initial()

    @property
    def sources(self) -> tuple[TickSource, ...]:
        """The sources this feeder merges."""
        return tuple(self._sources)

    def _push_initial(self) -> None:
        for index, source in enumerate(self._sources):
            tick = source.next_tick()
            if tick is not None:
                heapq.heappush(self._heap, (tick.time, tick.symbol, index, tick))

    def next_tick(self) -> Tick | None:
        """Return the next tick in chronological order, or ``None`` when exhausted."""
        if not self._heap:
            return None
        _, _, index, tick = heapq.heappop(self._heap)
        following = self._sources[index].next_tick()
        if following is not None:
            heapq.heappush(self._heap, (following.time, following.symbol, index, following))
        return tick

    def __iter__(self) -> Iterator[Tick]:
        """Every tick in chronological order."""
        while True:
            tick = self.next_tick()
            if tick is None:
                return
            yield tick

    def rewind_to(self, moment: datetime) -> None:
        """Position every source at the first tick at or after ``moment``."""
        for source in self._sources:
            source.rewind_to(moment)
        self._heap.clear()
        self._push_initial()

    def start_time(self) -> datetime:
        """The earliest tick time across the sources."""
        return min(source.start_time for source in self._sources)

    def end_time(self) -> datetime:
        """The latest tick time across the sources."""
        return max(source.end_time for source in self._sources)

    def common_range(self) -> tuple[datetime, datetime]:
        """The window in which every source has data.

        A backtest over a window in which one symbol has no quotes would silently
        produce a wrong equity curve, so the engine uses this range rather than the
        union of the sources.
        """
        return self.start_time(), self.end_time()
