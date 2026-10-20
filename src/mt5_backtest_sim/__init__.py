"""Tick-driven MetaTrader 5 style backtesting simulator.

The package is layered so that each layer can be tested on its own:

``domain``
    Pure value objects and enums. No I/O, no clock, no broker.
``market_data``
    Tick sources (CSV, the ``.ctf`` binary container) and the multi-symbol feeder.
``execution``
    Order lifecycle semantics: market and pending orders, stop loss, take profit,
    trailing stop, break even, OCO groups and partial closes.
``simulation``
    The engine that drives the execution layer over a tick stream.
``results``
    Statistics and report rendering.
``persistence``
    Settings and simulation save/load.
``integrations``
    The MetaTrader 5 bridge (named pipe protocol).
``cli``
    The command line entry point.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
