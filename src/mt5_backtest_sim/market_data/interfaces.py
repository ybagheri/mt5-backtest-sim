"""Tick source contract.

A tick source is a forward-only stream of quotes for one symbol with the ability to
jump back to an earlier point in time. Everything above this interface — the engine, the
broker, the strategies — works against the contract, never against a file format.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from mt5_backtest_sim.domain.models import Tick

__all__ = ["TickSource"]


@runtime_checkable
class TickSource(Protocol):
    """A replayable quote stream for a single symbol."""

    @property
    def symbol(self) -> str:
        """The symbol this source quotes."""
        ...

    @property
    def start_time(self) -> datetime:
        """Timestamp of the first available tick."""
        ...

    @property
    def end_time(self) -> datetime:
        """Timestamp of the last available tick."""
        ...

    def next_tick(self) -> Tick | None:
        """Return the next tick, or ``None`` when the source is exhausted."""
        ...

    def rewind_to(self, moment: datetime) -> None:
        """Position the source at the first tick at or after ``moment``."""
        ...
