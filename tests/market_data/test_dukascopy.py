"""The Dukascopy downloader.

The network is injected, so the whole download path is exercised without a server:
the test builds the very bytes Dukascopy serves and hands them to the downloader.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.market_data.ctf import CtfTickFile
from mt5_backtest_sim.market_data.dukascopy import (
    DUKASCOPY_SYMBOLS,
    bi5_url,
    download_ticks,
    find_symbol,
    parse_bi5,
)

HOUR = datetime(2022, 1, 3, 10, 0, tzinfo=UTC)


def build_bi5(ticks: list[tuple[int, int, int]]) -> bytes:
    """Build a bi5 payload from (millisecond, ask, bid) triples."""
    raw = b"".join(
        struct.pack(">IIIf f", ms, ask, bid, 1.0, 1.0) for ms, ask, bid in ticks
    )
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_the_catalog_knows_the_major_pairs() -> None:
    assert find_symbol("EURUSD").digits == 5
    assert find_symbol("usdjpy").digits == 3
    assert find_symbol("USDJPY").point == Decimal("0.001")
    assert len(DUKASCOPY_SYMBOLS) > 30


def test_an_unknown_symbol_is_reported() -> None:
    with pytest.raises(SimulationError):
        find_symbol("NOSUCH")


def test_the_url_of_an_hour_has_the_documented_shape() -> None:
    url = bi5_url(find_symbol("EURUSD"), HOUR)
    assert url == (
        "https://datafeed.dukascopy.com/datafeed/EURUSD/2022/01/03/10h_ticks.bi5"
    )


def test_a_payload_decodes_into_ticks() -> None:
    symbol = find_symbol("EURUSD")
    payload = build_bi5([(0, 110010, 110000), (1500, 110020, 110005)])

    ticks = parse_bi5(payload, symbol, HOUR)

    assert len(ticks) == 2
    assert ticks[0].time == HOUR
    assert ticks[0].bid == Decimal("1.10000")
    assert ticks[0].ask == Decimal("1.10010")
    assert ticks[1].time == HOUR.replace(second=1, microsecond=500000)
    assert ticks[1].bid == Decimal("1.10005")


def test_an_empty_payload_decodes_into_nothing() -> None:
    assert parse_bi5(b"", find_symbol("EURUSD"), HOUR) == []


def test_a_broken_payload_is_reported() -> None:
    with pytest.raises(SimulationError):
        parse_bi5(b"not an lzma stream", find_symbol("EURUSD"), HOUR)


def test_a_download_writes_a_readable_container(tmp_path: Path) -> None:
    symbol = find_symbol("EURUSD")
    payloads = {
        bi5_url(symbol, HOUR): build_bi5([(0, 110010, 110000), (1000, 110020, 110005)]),
        bi5_url(symbol, HOUR.replace(hour=11)): b"",
    }

    def fetcher(url: str) -> bytes:
        return payloads.get(url, b"")

    path, written = download_ticks(
        symbol,
        HOUR,
        HOUR.replace(hour=11),
        tmp_path / "sim_ticks_EURUSD.ctf",
        fetcher=fetcher,
    )

    assert written == 2
    with CtfTickFile(path, symbol="EURUSD") as reader:
        ticks = list(reader.ticks())
    assert len(ticks) == 2
    assert ticks[0].bid == Decimal("1.10000")
    assert reader.header.digits == 5


def test_the_downloader_skips_hours_without_data(tmp_path: Path) -> None:
    symbol = find_symbol("EURUSD")
    path, written = download_ticks(
        symbol,
        HOUR,
        HOUR.replace(hour=5),
        tmp_path / "empty.ctf",
        fetcher=lambda _url: b"",
    )
    assert written == 0
    assert path.is_file()
