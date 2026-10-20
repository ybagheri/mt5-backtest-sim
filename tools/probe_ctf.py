"""Decode the real tick history and report what it contains."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from mt5_backtest_sim.market_data.ctf import CtfTickFile

PATH = Path(
    r"C:\Users\BazikadeStore\Documents\ForexSimulator_Data\sim_ticks_EURUSD.ctf"
)

started = time.perf_counter()
with CtfTickFile(PATH, symbol="EURUSD") as reader:
    print("header:", reader.header)
    print("start:", reader.header.start_time, "end:", reader.header.end_time)
    print("digits:", reader.header.digits)

    count = 0
    first = None
    last = None
    min_bid = None
    max_bid = None
    out_of_order = 0
    previous_time = 0
    spreads: dict[int, int] = {}

    for tick in reader.ticks():
        if first is None:
            first = tick
        last = tick
        count += 1
        if tick.time.timestamp() < previous_time:
            out_of_order += 1
        previous_time = tick.time.timestamp()
        if min_bid is None or tick.bid < min_bid:
            min_bid = tick.bid
        if max_bid is None or tick.bid > max_bid:
            max_bid = tick.bid
        if count <= 5 or count % 5_000_000 == 0:
            print(f"  #{count:>12,}  {tick.time.isoformat()}  bid={tick.bid} ask={tick.ask}")
        if count >= 5_000_000:
            break

    print()
    print("ticks read      :", f"{count:,}")
    print("elapsed         :", f"{time.perf_counter() - started:.1f}s")
    print("first tick      :", first)
    print("last tick       :", last)
    print("bid range       :", min_bid, "-", max_bid)
    print("out of order    :", out_of_order)
    print(
        "coverage        :",
        (last.time - first.time).days if first and last else 0,
        "days",
    )
