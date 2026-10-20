"""Profile the hot loop."""

from __future__ import annotations

import cProfile
import pstats
import sys
from datetime import datetime

sys.path.insert(0, "src")

from mt5_backtest_sim.market_data.ctf import CtfTickFile

PATH = r"C:\Users\BazikadeStore\Documents\ForexSimulator_Data\sim_ticks_EURUSD.ctf"
LIMIT = 1_500_000


def run() -> None:
    count = 0
    with CtfTickFile(PATH, symbol="EURUSD") as reader:
        reader.seek_to(datetime(2022, 1, 3, tzinfo=reader.header.start_time.tzinfo))
        for _tick in reader.ticks():
            count += 1
            if count >= LIMIT:
                break
    print("ticks:", count)


profiler = cProfile.Profile()
profiler.enable()
run()
profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats("tottime").print_stats(14)
