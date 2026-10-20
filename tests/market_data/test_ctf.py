"""The ``.ctf`` container: round trip, header and seeking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.domain.models import Tick
from mt5_backtest_sim.market_data.ctf import CtfTickFile, CtfTickWriter, day_number

BASE = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


def make_ticks(count: int = 500) -> list[Tick]:
    """Build a plausible tick series."""
    ticks: list[Tick] = []
    bid = Decimal("1.10000")
    for index in range(count):
        step = Decimal(index % 7) - Decimal(3)
        bid = bid + step * Decimal("0.00001")
        ticks.append(
            Tick(
                time=BASE + timedelta(seconds=index),
                symbol="EURUSD",
                bid=bid,
                ask=bid + Decimal("0.00010"),
            )
        )
    return ticks


def test_round_trip_preserves_ticks(tmp_path: Path) -> None:
    ticks = make_ticks()
    path = tmp_path / "ticks.ctf"
    with CtfTickWriter(path, digits=5) as writer:
        for tick in ticks:
            writer.add_tick(tick)

    with CtfTickFile(path, symbol="EURUSD") as reader:
        read_back = list(reader.ticks())

    assert len(read_back) == len(ticks)
    for original, restored in zip(ticks, read_back, strict=True):
        assert restored.time == original.time
        assert restored.bid == original.bid
        assert restored.ask == original.ask


def test_header_reports_the_day_range(tmp_path: Path) -> None:
    path = tmp_path / "ticks.ctf"
    with CtfTickWriter(path, digits=5) as writer:
        for tick in make_ticks(10):
            writer.add_tick(tick)

    with CtfTickFile(path) as reader:
        assert reader.header.digits == 5
        assert reader.header.first_day == day_number(BASE)
        assert reader.header.last_day == day_number(BASE)


def test_seek_skips_earlier_ticks(tmp_path: Path) -> None:
    ticks = make_ticks(500)
    path = tmp_path / "ticks.ctf"
    with CtfTickWriter(path, digits=5) as writer:
        for tick in ticks:
            writer.add_tick(tick)

    with CtfTickFile(path) as reader:
        reader.seek_to(ticks[300].time)
        first = reader.next_tick()

    assert first is not None
    assert first.time >= ticks[300].time


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SimulationError):
        CtfTickFile(tmp_path / "absent.ctf")


def test_a_truncated_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.ctf"
    path.write_bytes(b"\0" * 32)
    with pytest.raises(SimulationError):
        CtfTickFile(path)
