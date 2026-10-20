"""Simulation configuration.

:class:`SimulationSettings` is the immutable description of *what* to simulate. It is
passed by value into the engine, so two runs with two settings objects cannot
influence each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from mt5_backtest_sim.domain.enums import TimeZone
from mt5_backtest_sim.domain.value_objects import Money

__all__ = ["SimulationSettings"]


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Everything that describes one simulation run."""

    starting_balance: Money = field(default_factory=lambda: Money(Decimal("10000")))
    leverage: int = 100
    commission_per_lot: Decimal = Decimal(0)
    margin_warning_level: Decimal = Decimal(30)
    starting_time: datetime | None = None
    ending_time: datetime | None = None
    time_zone: TimeZone = TimeZone.UTC
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.leverage <= 0:
            raise ValueError(f"leverage must be positive, got {self.leverage}")
        if self.starting_balance.value < 0:
            raise ValueError(
                f"starting_balance must not be negative, got {self.starting_balance}"
            )
        if self.commission_per_lot < 0:
            raise ValueError(
                f"commission_per_lot must not be negative, got {self.commission_per_lot}"
            )
        if (
            self.starting_time is not None
            and self.ending_time is not None
            and self.ending_time <= self.starting_time
        ):
            raise ValueError(
                f"ending_time {self.ending_time} is not after starting_time "
                f"{self.starting_time}"
            )
