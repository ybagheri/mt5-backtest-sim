"""The multi-symbol feeder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from mt5_backtest_sim.market_data.csv_source import CsvTickSource
from mt5_backtest_sim.market_data.feeder import TickFeeder

BASE = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


def write_csv(path: Path, rows: list[tuple[int, str, str]]) -> None:
    """Write a small tick CSV."""
    lines = ["time,bid,ask"]
    for second, bid, ask in rows:
        stamp = (BASE + timedelta(seconds=second)).isoformat()
        lines.append(f"{stamp},{bid},{ask}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_ticks_are_merged_in_chronological_order(tmp_path: Path) -> None:
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    write_csv(first, [(0, "1.10000", "1.10010"), (2, "1.10002", "1.10012")])
    write_csv(second, [(1, "2.20000", "2.20010"), (3, "2.20002", "2.20012")])

    feeder = TickFeeder(
        [
            CsvTickSource(first, symbol="EURUSD"),
            CsvTickSource(second, symbol="GBPUSD"),
        ]
    )
    merged = list(feeder)
    times = [tick.time for tick in merged]
    assert times == sorted(times)
    assert [tick.symbol for tick in merged] == ["EURUSD", "GBPUSD", "EURUSD", "GBPUSD"]


def test_same_second_ticks_are_ordered_by_symbol(tmp_path: Path) -> None:
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    write_csv(first, [(0, "1.10000", "1.10010")])
    write_csv(second, [(0, "2.20000", "2.20010")])

    feeder = TickFeeder(
        [
            CsvTickSource(second, symbol="GBPUSD"),
            CsvTickSource(first, symbol="EURUSD"),
        ]
    )
    merged = list(feeder)
    assert [tick.symbol for tick in merged] == ["EURUSD", "GBPUSD"]


def test_rewind_replays_from_the_moment(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    write_csv(path, [(0, "1.10000", "1.10010"), (5, "1.10005", "1.10015")])
    source = CsvTickSource(path, symbol="EURUSD")
    feeder = TickFeeder([source])

    assert feeder.next_tick() is not None
    feeder.rewind_to(BASE + timedelta(seconds=3))
    tick = feeder.next_tick()
    assert tick is not None
    assert tick.time == BASE + timedelta(seconds=5)


def test_a_csv_without_the_expected_columns_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("time,price\n2024-01-02T00:00:00Z,1.1\n", encoding="utf-8")

    from mt5_backtest_sim.domain.exceptions import SimulationError

    with pytest.raises(SimulationError):
        CsvTickSource(path, symbol="EURUSD")


import pytest  # noqa: E402  (imported late so the file reads top-down)
