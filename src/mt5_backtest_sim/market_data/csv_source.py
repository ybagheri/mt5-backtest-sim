"""CSV tick source.

The simplest useful source, and the format most third-party tick exports use:

.. code-block:: text

    time,bid,ask
    2024-01-02T00:00:00+00:00,1.10425,1.10445

The parser is deliberately forgiving about the timestamp spelling — ISO 8601 with or
without an offset, and a plain Unix epoch in seconds or milliseconds — because tick
exports come from many tools and the timestamp is not the interesting part. Prices are
parsed as :class:`~decimal.Decimal` from the original text, never through ``float``.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.domain.models import Tick

__all__ = ["CsvTickSource", "parse_timestamp"]


def parse_timestamp(text: str) -> datetime:
    """Parse a tick timestamp into timezone-aware UTC.

    Accepts ISO 8601 (with or without an offset), ``YYYY-MM-DD HH:MM:SS`` and Unix
    epochs in seconds or milliseconds. A timestamp without an offset is read as UTC,
    because a tick file that does not record its offset cannot mean anything else
    reliably.
    """
    raw = text.strip()
    if not raw:
        raise SimulationError("empty timestamp")

    if raw.replace(".", "", 1).isdigit():
        number = Decimal(raw)
        seconds = number / 1000 if number > Decimal("1e11") else number
        return datetime.fromtimestamp(float(seconds), tz=UTC)

    normalised = raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise SimulationError(f"cannot parse timestamp {text!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class CsvTickSource:
    """Replay quotes from a CSV file."""

    def __init__(
        self,
        path: str | Path,
        symbol: str,
        time_column: str = "time",
        bid_column: str = "bid",
        ask_column: str = "ask",
    ) -> None:
        self.path = Path(path)
        self._symbol = symbol
        self._time_column = time_column
        self._bid_column = bid_column
        self._ask_column = ask_column
        self._ticks: list[Tick] = []
        self._index = 0
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            raise SimulationError(f"tick file not found: {self.path}")
        rows: list[Tick] = []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise SimulationError(f"tick file {self.path} has no header row")
            missing = {
                name
                for name in (self._time_column, self._bid_column, self._ask_column)
                if name not in reader.fieldnames
            }
            if missing:
                raise SimulationError(
                    f"tick file {self.path} is missing column(s) {sorted(missing)}"
                )
            for line_number, row in enumerate(reader, start=2):
                try:
                    tick = Tick(
                        time=parse_timestamp(row[self._time_column]),
                        symbol=self._symbol,
                        bid=Decimal(row[self._bid_column].strip()),
                        ask=Decimal(row[self._ask_column].strip()),
                    )
                except (SimulationError, ValueError) as exc:
                    raise SimulationError(
                        f"{self.path}:{line_number}: {exc}"
                    ) from exc
                rows.append(tick)
        rows.sort(key=lambda tick: tick.time)
        self._ticks = rows

    @property
    def symbol(self) -> str:
        """The symbol this source quotes."""
        return self._symbol

    @property
    def start_time(self) -> datetime:
        """Timestamp of the first available tick."""
        if not self._ticks:
            raise SimulationError(f"tick file {self.path} contains no ticks")
        return self._ticks[0].time

    @property
    def end_time(self) -> datetime:
        """Timestamp of the last available tick."""
        if not self._ticks:
            raise SimulationError(f"tick file {self.path} contains no ticks")
        return self._ticks[-1].time

    def next_tick(self) -> Tick | None:
        """Return the next tick, or ``None`` when the source is exhausted."""
        if self._index >= len(self._ticks):
            return None
        tick = self._ticks[self._index]
        self._index += 1
        return tick

    def rewind_to(self, moment: datetime) -> None:
        """Position the source at the first tick at or after ``moment``."""
        low, high = 0, len(self._ticks)
        while low < high:
            middle = (low + high) // 2
            if self._ticks[middle].time < moment:
                low = middle + 1
            else:
                high = middle
        self._index = low

    def __len__(self) -> int:
        return len(self._ticks)
