# mt5-backtest-sim

A tick-driven MetaTrader 5 style backtesting simulator, written in Python.

The project exists to answer one question properly: *what would this strategy have
made on this tick history?* Everything in it — the order lifecycle, the money
arithmetic, the statistics, the report — is built so that the answer is reproducible
and can be checked by hand.

## Why it looks like this

The reference for the behaviour is Soft4FX's Forex Simulator MT5, a commercial desktop
application for MetaTrader 5. That product was studied to understand how a retail
trader interacts with a terminal: a market and pending order model, a spread-aware
fill, stop loss and take profit evaluated on the quote, break-even and trailing stop,
margin reserved per symbol, and a trade blotter that can be exported.

This package is a **clean-room re-implementation** of those semantics, not a port. No
code was copied; the behaviour was derived from the observed product and from public
MetaTrader 5 documentation. Where the reference product was ambiguous or lossy, this
implementation states its choice explicitly:

| Concern | Choice here |
| --- | --- |
| Prices | `Decimal` everywhere; money is never a float |
| Prices on the wire | integer points, converted only at the boundary |
| Fill price | buy at the ask, sell at the bid — the spread is real money |
| Stop loss and take profit | evaluated on the closing quote (bid for a long, ask for a short) and filled at the level itself |
| Commission | charged per lot, on the balance when the trade closes |
| Margin | reserved per symbol from the open price, released on close |
| Look-ahead bias | the engine is tick-driven; a bar is only closed when its successor opens |
| Global state | none — the engine, the broker and the account are ordinary objects |

## Layout

```
src/mt5_backtest_sim/
  domain/         value objects, enums, models, exceptions  (no I/O, no clock)
  market_data/    tick sources: CSV, the .ctf container, and the multi-symbol feeder
  execution/      the simulated broker and the operation ledger
  simulation/     the engine and its settings
  strategy/       the strategy contract
  results/        statistics and HTML report rendering
  persistence/    settings and simulation save/load
  integrations/   the MetaTrader 5 bridge
  cli/            the command line entry point
examples/         example strategies
tools/            profiling and inspection helpers
```

## Install

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"      # Windows
.venv/bin/pip install -e ".[dev]"          # Linux and macOS
```

The core is stdlib-only. `MetaTrader5` is an optional extra and is not needed to run
a backtest.

## Use

Inspect a tick container:

```bash
.venv\Scripts\python -m mt5_backtest_sim.cli.main inspect \
    --ticks "C:\path\to\sim_ticks_EURUSD.ctf" --count
```

Replay a history through a strategy and write a report:

```bash
.venv\Scripts\python -m mt5_backtest_sim.cli.main run \
    --ticks "C:\path\to\sim_ticks_EURUSD.ctf" \
    --strategy "examples.ma_crossover:MovingAverageCrossover" \
    --start 2022-01-03T00:00:00Z --end 2022-06-30T00:00:00Z \
    --balance 10000 --leverage 100 --commission 3.5 \
    --report out/report.html --json out/summary.json
```

A strategy is any object with an `on_tick(tick, context)` method:

```python
class MyStrategy:
    def on_tick(self, tick, context):
        if not context.open_positions():
            context.broker.open_market_order(
                tick.symbol, OrderType.BUY, Volume.from_lots("0.10")
            )
```

## The `.ctf` tick container

`.ctf` is the binary tick container used by the Forex Simulator MT5 data centre. It is
read here so that the same tick history can be replayed by this tool:

```
offset 0        int32  first day (days since 1970-01-01)
offset 4        int32  last day
offset 8        int32  price digits
offset 64       int64 x 30000   day offset table
offset 240064   tick payload
```

Each tick starts with one control byte carrying a time selector, a bid selector and an
ask selector. A selector of 0 to 6 encodes a small change against the previous value;
a selector of 7 means an absolute 32 bit value follows. Prices are integers scaled by
`10 ** -digits` and times are Unix seconds.

`market_data.ctf.CtfTickWriter` writes the same format, so a history can be converted
between the container and CSV without loss.

## Development

```bash
.venv\Scripts\python -m pytest            # tests
.venv\Scripts\python -m ruff check .       # lint
.venv\Scripts\python -m mypy               # types, strict
```

## Status

Working end to end: tick replay, order lifecycle, statistics, HTML report.

Not built yet: multi-timeframe bar aggregation, a persisted simulation format, a
desktop user interface, the MetaTrader 5 bridge, and the data downloader that fetches
history from the upstream providers.
