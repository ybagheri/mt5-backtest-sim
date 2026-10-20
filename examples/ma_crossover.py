"""Example strategies.

These are deliberately simple and are used by the documentation and the smoke test.
A strategy only needs the current tick and the broker.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from mt5_backtest_sim.domain.enums import OrderType
from mt5_backtest_sim.domain.models import Tick
from mt5_backtest_sim.domain.value_objects import Volume
from mt5_backtest_sim.strategy.base import StrategyContext

__all__ = ["BuyAndHold", "MovingAverageCrossover"]


class MovingAverageCrossover:
    """Trade the crossover of a fast and a slow time-windowed moving average.

    The strategy buys when the fast average crosses above the slow one and sells when
    it crosses below. It keeps one position at a time and protects it with a stop loss
    and a take profit expressed in pips.

    The averages are computed in floating point on purpose: an indicator is an
    approximation and never becomes money directly, while the money in the account is
    exact. Keeping the indicator in floating point roughly doubles the replay speed on
    a multi-million tick history.
    """

    def __init__(
        self,
        fast_seconds: int = 300,
        slow_seconds: int = 3600,
        volume_lots: str = "0.10",
        stop_loss_pips: int = 30,
        take_profit_pips: int = 60,
    ) -> None:
        self.fast_seconds = fast_seconds
        self.slow_seconds = slow_seconds
        self.volume_lots = volume_lots
        self.stop_loss_pips = stop_loss_pips
        self.take_profit_pips = take_profit_pips
        self._fast: deque[tuple[datetime, float]] = deque()
        self._slow: deque[tuple[datetime, float]] = deque()
        self._fast_sum = 0.0
        self._slow_sum = 0.0
        self._previous: tuple[float, float] | None = None

    def on_tick(self, tick: Tick, context: StrategyContext) -> None:
        """Update the averages and act on a crossover."""
        price = float(tick.mid)
        fast = self._fast
        slow = self._slow
        fast.append((tick.time, price))
        slow.append((tick.time, price))
        fast_sum = self._fast_sum + price
        slow_sum = self._slow_sum + price
        cutoff_fast = tick.time - timedelta(seconds=self.fast_seconds)
        while fast and fast[0][0] < cutoff_fast:
            fast_sum -= fast.popleft()[1]
        cutoff_slow = tick.time - timedelta(seconds=self.slow_seconds)
        while slow and slow[0][0] < cutoff_slow:
            slow_sum -= slow.popleft()[1]
        self._fast_sum = fast_sum
        self._slow_sum = slow_sum

        if not fast or not slow:
            return
        fast_average = fast_sum / len(fast)
        slow_average = slow_sum / len(slow)
        previous = self._previous
        self._previous = (fast_average, slow_average)
        if previous is None:
            return
        previous_fast, previous_slow = previous
        crossed_up = previous_fast <= previous_slow and fast_average > slow_average
        crossed_down = previous_fast >= previous_slow and fast_average < slow_average
        if crossed_up or crossed_down:
            self._act(tick, context, crossed_up)

    def _act(self, tick: Tick, context: StrategyContext, crossed_up: bool) -> None:
        state = context.symbol(tick.symbol)
        spec = state.spec
        positions = context.open_positions()
        if crossed_up and not positions:
            stop = tick.ask - spec.pip_size * self.stop_loss_pips
            target = tick.ask + spec.pip_size * self.take_profit_pips
            context.broker.open_market_order(
                tick.symbol,
                OrderType.BUY,
                Volume.from_lots(self.volume_lots),
                stop_loss=stop,
                take_profit=target,
            )
        elif not crossed_up and positions:
            for position in positions:
                if position.symbol == tick.symbol:
                    context.broker.close_market_order(position.ticket)


class BuyAndHold:
    """Open one position on the first tick and never touch it again."""

    def __init__(self, volume_lots: str = "0.10") -> None:
        self.volume_lots = volume_lots
        self._entered = False

    def on_tick(self, tick: Tick, context: StrategyContext) -> None:
        """Enter once."""
        if self._entered:
            return
        self._entered = True
        context.broker.open_market_order(
            tick.symbol,
            OrderType.BUY,
            Volume.from_lots(self.volume_lots),
        )
