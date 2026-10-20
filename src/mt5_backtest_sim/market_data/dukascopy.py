"""Tick history from Dukascopy.

Dukascopy publishes its tick history as hourly *bi5* files: an LZMA stream of fixed
size records, one record per tick. The record layout is:

===========  ======  ==========================================
offset       size    meaning
===========  ======  ==========================================
0            4       milliseconds since the start of the hour
4            4       ask price, big endian, scaled by the point
8            4       bid price, big endian, scaled by the point
12           4       ask volume, IEEE 754 float
16           4       bid volume, IEEE 754 float
===========  ======  ==========================================

The downloader writes the ticks into the ``.ctf`` container of this package, so a
history fetched here can be replayed by the simulator and read by the reference
product.

The network access is injected. That keeps the parsing testable without a network and
keeps the retry and rate limit policy in one place.
"""

from __future__ import annotations

import lzma
import struct
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.domain.models import Tick
from mt5_backtest_sim.market_data.ctf import CtfTickWriter

__all__ = [
    "DUKASCOPY_SYMBOLS",
    "DukascopySymbol",
    "bi5_url",
    "download_ticks",
    "parse_bi5",
]

BASE_URL: Final = "https://datafeed.dukascopy.com/datafeed"
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TICK_RECORD_SIZE: Final = 20


@dataclass(frozen=True, slots=True)
class DukascopySymbol:
    """One instrument as Dukascopy publishes it."""

    name: str
    digits: int = 5
    point: Decimal = Decimal("0.00001")

    @property
    def scale(self) -> Decimal:
        """The factor that turns the stored integer into a price."""
        return self.point


#: The instruments this package knows how to fetch.
DUKASCOPY_SYMBOLS: tuple[DukascopySymbol, ...] = tuple(
    [DukascopySymbol(name) for name in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
                                        "USDCAD", "USDCHF", "EURGBP", "EURJPY",
                                        "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
                                        "EURAUD", "EURCAD", "EURCHF", "EURNZD",
                                        "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
                                        "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF",
                                        "NZDCAD", "NZDCHF", "USDSGD", "USDHKD",
                                        "USDTRY", "USDZAR", "USDMXN", "USDPLN",
                                        "EURSEK", "EURHUF", "EURTRY", "EURRUB")]
    + [
        DukascopySymbol("USDJPY", 3, Decimal("0.001")),
        DukascopySymbol("EURJPY", 3, Decimal("0.001")),
        DukascopySymbol("GBPJPY", 3, Decimal("0.001")),
        DukascopySymbol("AUDJPY", 3, Decimal("0.001")),
        DukascopySymbol("CADJPY", 3, Decimal("0.001")),
        DukascopySymbol("CHFJPY", 3, Decimal("0.001")),
    ]
    + [
        DukascopySymbol(name) for name in ("XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
                                           "BRENT", "WTI", "ASX200", "SMI20",
                                           "DAX30", "IBEX35", "STOXX", "CAC40",
                                           "FTSE", "HSI", "JP225", "DJI30",
                                           "SP500", "NQ100", "DXY")
    ]
)


def find_symbol(name: str) -> DukascopySymbol:
    """Look up one instrument by name."""
    for symbol in DUKASCOPY_SYMBOLS:
        if symbol.name == name.upper():
            return symbol
    raise SimulationError(f"unknown Dukascopy symbol {name!r}")


def bi5_url(symbol: DukascopySymbol, moment: datetime) -> str:
    """The address of the hourly file that holds the ticks of ``moment``."""
    return (
        f"{BASE_URL}/{symbol.name}/{moment:%Y}/{moment:%m}/{moment:%d}/"
        f"{moment:%Hh}_ticks.bi5"
    )


def parse_bi5(payload: bytes, symbol: DukascopySymbol, hour: datetime) -> list[Tick]:
    """Decode one hourly file into ticks.

    The file is an LZMA stream of fixed size records. An empty file is normal: the
    market was closed, and there is nothing to decode.
    """
    if not payload:
        return []
    try:
        raw = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError as exc:
        raise SimulationError(f"cannot decompress the bi5 payload: {exc}") from exc

    ticks: list[Tick] = []
    start = hour.replace(minute=0, second=0, microsecond=0)
    for offset in range(0, len(raw) - TICK_RECORD_SIZE + 1, TICK_RECORD_SIZE):
        (
            milliseconds,
            ask,
            bid,
            _ask_volume,
            _bid_volume,
        ) = struct.unpack_from(">IIIf f", raw, offset)
        moment = start + timedelta(milliseconds=milliseconds)
        ticks.append(
            Tick(
                time=moment,
                symbol=symbol.name,
                bid=Decimal(bid) * symbol.scale,
                ask=Decimal(ask) * symbol.scale,
            )
        )
    return ticks


def _default_fetcher(url: str, timeout: float = 30.0) -> bytes:
    """Fetch one URL and return its body."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return b""
        raise SimulationError(f"cannot fetch {url}: HTTP {exc.code}") from exc
    except OSError as exc:
        raise SimulationError(f"cannot fetch {url}: {exc}") from exc


def download_ticks(
    symbol: DukascopySymbol,
    start: datetime,
    end: datetime,
    output_path: str | Path,
    fetcher: Callable[[str], bytes] | None = None,
    hours: Iterator[datetime] | None = None,
) -> tuple[Path, int]:
    """Fetch a history and write it into a ``.ctf`` container.

    Returns the path and the number of ticks written. Hours without data are skipped:
    the weekend and the holidays simply have no file.
    """
    fetch = fetcher if fetcher is not None else _default_fetcher
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with CtfTickWriter(target, digits=symbol.digits) as writer:
        for moment in hours if hours is not None else _hours(start, end):
            payload = fetch(bi5_url(symbol, moment))
            if not payload:
                continue
            for tick in parse_bi5(payload, symbol, moment):
                writer.add_tick(tick)
                written += 1
    return target, written


def _hours(start: datetime, end: datetime) -> Iterator[datetime]:
    """Every hour between two moments, both in UTC."""
    current = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    finish = end.astimezone(UTC)
    while current <= finish:
        yield current
        current += timedelta(hours=1)
