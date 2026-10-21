"""Driving the terminal with the simulated market.

The bridge is not only a question and answer channel: the simulator can push the
market it replays into the terminal, so an indicator or an expert in the terminal
sees the same prices the simulator uses.

:class:`LiveBridge` connects the two: it sends the symbol list once, then forwards
the quotes of every tick and the bars the engine built.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mt5_backtest_sim.domain.enums import Timeframe
from mt5_backtest_sim.domain.models import Candle, Tick
from mt5_backtest_sim.integrations.mt5.protocol import Bar, SymbolDescription, SymbolQuote
from mt5_backtest_sim.integrations.mt5.session import Mt5Session

__all__ = ["LiveBridge"]


@dataclass(slots=True)
class LiveBridge:
    """Forwards a replay to the terminal."""

    session: Mt5Session
    symbol: str = "EURUSD"
    timeframe: Timeframe = Timeframe.M1
    bars_sent: int = 0
    ticks_sent: int = 0
    _started: bool = field(default=False, init=False)

    def start(
        self,
        symbols: list[SymbolDescription] | None = None,
        account_currency: str = "USD",
    ) -> None:
        """Tell the terminal what the run covers."""
        described = symbols or [
            SymbolDescription(self.symbol, self.symbol[:3], self.symbol[3:], 5, 10, 100000)
        ]
        self.session.send_symbols(described, account_currency)
        self._started = True

    def on_tick(self, tick: Tick) -> None:
        """Forward one tick as the current quote of the symbol."""
        if not self._started:
            self.start()
        self.session.send_rates(
            tick.time,
            [SymbolQuote(tick.symbol, float(tick.bid), float(tick.ask))],
        )
        self.ticks_sent += 1

    def on_bars(self, symbol: str, bars: list[Candle]) -> None:
        """Forward the bars the engine built."""
        payload = [
            Bar(
                int(bar.open_time.timestamp()),
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                bar.tick_volume,
            )
            for bar in bars
        ]
        self.session.send_bars(symbol, self.timeframe.seconds, payload)
        self.bars_sent += len(payload)

    def report(self) -> str:
        """A short summary of what was sent."""
        return (
            f"sent {self.ticks_sent} quotes and {self.bars_sent} bars "
            f"for {self.symbol}"
        )
