"""Strategy contract.

A strategy is a callable that reacts to ticks. It is handed a
:class:`StrategyContext` and returns nothing: everything it wants to do, it does by
calling the broker through the context.

The contract is deliberately tiny. A strategy that can only see the current tick and
place orders is enough to express a trading system, and a contract that small can be
tested without a terminal, a broker or a data feed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mt5_backtest_sim.domain.models import Account, Order, SymbolState, Tick
from mt5_backtest_sim.execution.broker import SimulatedBroker

__all__ = ["Strategy", "StrategyContext"]


class StrategyContext:
    """Everything a strategy is allowed to touch."""

    def __init__(self, broker: SimulatedBroker) -> None:
        self.broker = broker

    @property
    def account(self) -> Account:
        """The simulated account."""
        return self.broker.account

    def symbol(self, name: str) -> SymbolState:
        """The live state of one symbol."""
        return self.broker.symbols[name]

    def open_positions(self) -> tuple[Order, ...]:
        """The open positions."""
        return tuple(self.broker.market_orders)

    def pending_orders(self) -> tuple[Order, ...]:
        """The resting orders."""
        return tuple(self.broker.pending_orders)


@runtime_checkable
class Strategy(Protocol):
    """Reacts to ticks."""

    def on_tick(self, tick: Tick, context: StrategyContext) -> None:
        """Called once per tick, before the broker processes it."""
        ...
