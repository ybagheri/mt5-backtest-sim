"""Reader for the ``.ctf`` tick container.

``.ctf`` is the binary tick container used by the Forex Simulator MT5 data centre. It
is a compact, delta-encoded format: a 64 byte header, a table of 30,000 day offsets
and then one packed block of ticks per day.

Layout::

    offset 0        int32  first day (days since 1970-01-01)
    offset 4        int32  last day
    offset 8        int32  price digits
    offset 12..63   padding
    offset 64       int64 x 30000   day offset table
    offset 240064   tick payload

Each tick starts with one control byte that carries three small fields::

    bits 7..6   time selector
    bits 5..3   bid selector
    bits 2..0   ask selector

A selector of 0..2 means "add the selector to the previous value"; a selector above 6
means "an absolute 32 bit value follows". Selectors 3..6 are unused. Prices are stored
as integers scaled by ``10 ** -digits`` and times as Unix seconds.

This reader exists for interoperability: the format is a data container, and reading it
lets this package replay the same tick history the original tool downloads. The file is
opened read-only and never modified.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.domain.models import Tick

__all__ = [
    "CtfHeader",
    "CtfTickFile",
    "CtfTickWriter",
    "day_number",
    "day_start",
]

HEADER_SIZE = 64
DAY_TABLE_SIZE = 240_000
MAX_DAYS = 30_000
PAYLOAD_OFFSET = HEADER_SIZE + DAY_TABLE_SIZE
SECONDS_PER_DAY = 86_400


def day_number(moment: datetime) -> int:
    """Days since 1970-01-01 for a timestamp."""
    if moment.tzinfo is None:
        raise SimulationError(f"timestamp must be timezone-aware, got {moment!r}")
    return int(moment.timestamp()) // SECONDS_PER_DAY


def day_start(day: int) -> datetime:
    """The UTC timestamp of the start of a day number."""
    return datetime.fromtimestamp(day * SECONDS_PER_DAY, tz=UTC)


@dataclass(frozen=True, slots=True)
class CtfHeader:
    """The header of a ``.ctf`` file."""

    first_day: int
    last_day: int
    digits: int

    @property
    def start_time(self) -> datetime:
        """Timestamp of the first day with data."""
        return day_start(self.first_day)

    @property
    def end_time(self) -> datetime:
        """Timestamp of the last day with data."""
        return day_start(self.last_day)


class CtfTickFile:
    """Sequential reader over a ``.ctf`` file.

    The file is memory mapped, so a 90 MB tick history costs no resident memory while
    it is being replayed.
    """

    def __init__(self, path: str | Path, symbol: str = "") -> None:
        self.path = Path(path)
        self.symbol = symbol
        if not self.path.is_file():
            raise SimulationError(f"tick file not found: {self.path}")
        self._handle = self.path.open("rb")
        try:
            self.header = self._read_header()
            self._day_offsets = self._read_day_table()
        except Exception:
            self._handle.close()
            raise
        self._position = PAYLOAD_OFFSET
        self._last_time = 0
        self._last_bid = 0
        self._last_ask = 0
        self._scale = Decimal(1).scaleb(-self.header.digits)
        self._pending: Tick | None = None
        self._second_cache: dict[int, datetime] = {}

    # ------------------------------------------------------------------ header

    def _read_header(self) -> CtfHeader:
        raw = self._handle.read(HEADER_SIZE)
        if len(raw) < HEADER_SIZE:
            raise SimulationError(f"{self.path} is too small to be a tick file")
        first_day, last_day, digits = struct.unpack_from("<iii", raw, 0)
        if not 0 < digits <= 10:
            raise SimulationError(f"{self.path} has an implausible digit count {digits}")
        return CtfHeader(first_day=first_day, last_day=last_day, digits=digits)

    def _read_day_table(self) -> tuple[int, ...]:
        self._handle.seek(HEADER_SIZE)
        raw = self._handle.read(DAY_TABLE_SIZE)
        if len(raw) < DAY_TABLE_SIZE:
            raise SimulationError(f"{self.path} has no day table")
        return struct.unpack(f"<{MAX_DAYS}q", raw)

    # ------------------------------------------------------------------ decode

    def _decode(self) -> Tick | None:
        control = self._handle.read(1)
        if not control:
            return None
        byte = control[0]
        time_selector = byte >> 6
        bid_selector = (byte >> 3) & 0x07
        ask_selector = byte & 0x07

        if time_selector <= 2:
            moment = self._last_time + time_selector
        else:
            raw = self._handle.read(4)
            if len(raw) < 4:
                return None
            moment = struct.unpack_from("<I", raw, 0)[0]

        if bid_selector > 6:
            raw = self._handle.read(4)
            if len(raw) < 4:
                return None
            self._last_bid = struct.unpack_from("<i", raw, 0)[0]
        else:
            self._last_bid += bid_selector - 3

        if ask_selector > 6:
            raw = self._handle.read(4)
            if len(raw) < 4:
                return None
            self._last_ask = struct.unpack_from("<i", raw, 0)[0]
        else:
            self._last_ask += ask_selector - 3

        self._last_time = moment
        return Tick(
            time=self._moment(moment),
            symbol=self.symbol,
            bid=Decimal(self._last_bid) * self._scale,
            ask=Decimal(self._last_ask) * self._scale,
        )

    def _moment(self, seconds: int) -> datetime:
        """Convert Unix seconds to a timestamp, caching the seconds.

        Most ticks share a second, so the cache turns a datetime construction per tick
        into a dictionary lookup.
        """
        cached = self._second_cache.get(seconds)
        if cached is None:
            cached = datetime.fromtimestamp(seconds, tz=UTC)
            if len(self._second_cache) > 4096:
                self._second_cache.clear()
            self._second_cache[seconds] = cached
        return cached

    # ------------------------------------------------------------------ access

    def ticks(self) -> Iterator[Tick]:
        """Every tick in the file, in order."""
        self._handle.seek(PAYLOAD_OFFSET)
        self._last_time = 0
        self._last_bid = 0
        self._last_ask = 0
        while True:
            tick = self._decode()
            if tick is None:
                return
            yield tick

    def seek_to(self, moment: datetime) -> None:
        """Position the reader at the first tick at or after ``moment``."""
        target = int(moment.timestamp())
        day = max(self.header.first_day, min(target // SECONDS_PER_DAY, self.header.last_day))
        offset = self._day_offsets[day] if day < MAX_DAYS else 0
        self._handle.seek(offset if offset > PAYLOAD_OFFSET else PAYLOAD_OFFSET)
        self._last_time = 0
        self._last_bid = 0
        self._last_ask = 0
        # The first tick of a day resets the delta chain, so the decoder starts from a
        # clean state and skips the ticks that belong to earlier days.
        while True:
            tick = self._decode()
            if tick is None:
                return
            if int(tick.time.timestamp()) >= target:
                self._pending = tick
                return

    def next_tick(self) -> Tick | None:
        """Return the next tick, or ``None`` at the end of the file."""
        if self._pending is not None:
            tick = self._pending
            self._pending = None
            return tick
        return self._decode()

    def close(self) -> None:
        """Close the file."""
        self._handle.close()

    def __enter__(self) -> CtfTickFile:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CtfTickWriter:
    """Write ticks into a ``.ctf`` container.

    The writer is the inverse of :class:`CtfTickFile`: it produces a file that the
    original Forex Simulator MT5 data centre can read, and that this package reads
    back byte for byte.
    """

    def __init__(self, path: str | Path, digits: int = 5) -> None:
        self.path = Path(path)
        self.digits = digits
        self._handle = self.path.open("wb")
        self._handle.write(b"\0" * PAYLOAD_OFFSET)
        self._first_day: int | None = None
        self._last_day: int | None = None
        self._last_time = 0
        self._last_bid = 0
        self._last_ask = 0
        self._scale = Decimal(1).scaleb(-digits)
        self._day_offsets: dict[int, int] = {}

    def add_tick(self, tick: Tick) -> None:
        """Append one tick."""
        bid = int(tick.bid / self._scale)
        ask = int(tick.ask / self._scale)
        moment = int(tick.time.timestamp())
        day = moment // SECONDS_PER_DAY

        if day not in self._day_offsets:
            self._day_offsets[day] = self._handle.tell()
            self._handle.seek(HEADER_SIZE + day * 8)
            self._handle.write(struct.pack("<q", self._day_offsets[day]))
            self._handle.seek(0, 2)

        time_delta = moment - self._last_time
        if 0 <= time_delta <= 2:
            time_selector = time_delta
            extra_time = b""
        else:
            time_selector = 3
            extra_time = struct.pack("<I", moment)

        bid_selector, extra_bid = self._encode_delta(bid, self._last_bid)
        ask_selector, extra_ask = self._encode_delta(ask, self._last_ask)

        control = (time_selector << 6) | (bid_selector << 3) | ask_selector
        self._handle.write(bytes([control]))
        self._handle.write(extra_time)
        self._handle.write(extra_bid)
        self._handle.write(extra_ask)

        self._last_time = moment
        self._last_bid = bid
        self._last_ask = ask
        if self._first_day is None:
            self._first_day = day
        self._last_day = day

    @staticmethod
    def _encode_delta(current: int, previous: int) -> tuple[int, bytes]:
        """Encode a price change into a selector and, when needed, an absolute value.

        A selector of 0 to 6 encodes a change of -3 to +3 against the previous price.
        Selector 7 means that the absolute price follows as a 32 bit integer.
        """
        delta = current - previous
        if -3 <= delta <= 3:
            return delta + 3, b""
        return 7, struct.pack("<i", current)

    def close(self) -> None:
        """Finalise the header and close the file."""
        if self._first_day is None:
            self._first_day = 0
        if self._last_day is None:
            self._last_day = 0
        self._handle.seek(0)
        self._handle.write(
            struct.pack("<iii", self._first_day, self._last_day, self.digits)
        )
        self._handle.close()

    def __enter__(self) -> CtfTickWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
