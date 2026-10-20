"""The simulator side of the MetaTrader 5 bridge.

:class:`Mt5Session` owns the named pipe and speaks the protocol to the expert
advisor. It is the piece that lets the simulator ask the terminal for values the
terminal is better at computing â€” ATR above all â€” and lets it push the simulated
market to the terminal so an indicator in the terminal sees what the simulator sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.integrations.mt5.pipe_server import PipeServer
from mt5_backtest_sim.integrations.mt5.protocol import (
    MESSAGE_ID,
    Bar,
    PipeCodec,
    RefreshRatesMessage,
    SymbolDescription,
    SymbolQuote,
    SymbolsMessage,
    UpdateChartsMessage,
)

__all__ = ["Mt5Session"]


@dataclass(slots=True)
class Mt5Session:
    """A conversation with the expert advisor in the terminal."""

    pipe_name: str
    server: PipeServer | None = None

    def connect(self) -> None:
        """Wait for the expert to connect."""
        self.server = PipeServer(self.pipe_name)
        self.server.listen()

    def close(self) -> None:
        """Close the pipe."""
        if self.server is not None:
            self.server.close()
            self.server = None

    def __enter__(self) -> Mt5Session:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ send

    def _require_server(self) -> PipeServer:
        if self.server is None:
            raise SimulationError("the session is not connected")
        return self.server

    def send_symbols(
        self,
        symbols: list[SymbolDescription],
        account_currency: str = "USD",
        rewinding_enabled: bool = True,
    ) -> None:
        """Tell the terminal which symbols the run covers."""
        message = SymbolsMessage(symbols, rewinding_enabled, account_currency)
        self._require_server().send(message.encode())

    def send_rates(self, moment: datetime, quotes: list[SymbolQuote]) -> None:
        """Tell the terminal what the market is doing now."""
        message = RefreshRatesMessage(int(moment.timestamp()), quotes)
        self._require_server().send(message.encode())

    def send_bars(self, symbol: str, timeframe: int, bars: list[Bar]) -> None:
        """Send the bars the simulator built to the terminal."""
        message = UpdateChartsMessage(symbol, timeframe, bars)
        self._require_server().send(message.encode())

    def request_atr(self, symbol: str, timeframe: int, period: int) -> float:
        """Ask the terminal for the ATR of a symbol and read the answer."""
        server = self._require_server()
        payload = PipeCodec.encode_string(symbol)
        payload += PipeCodec.encode_int(timeframe)
        payload += PipeCodec.encode_int(period)
        server.send(PipeCodec.encode_int(int(MESSAGE_ID.GET_ATR)) + payload)
        answer = server.receive()
        if answer is None:
            raise SimulationError("the terminal closed the pipe while answering")
        if len(answer) < 8:
            raise SimulationError("the terminal sent a truncated ATR answer")
        return float(int.from_bytes(answer[0:8], "little", signed=True))

    def ping(self) -> bool:
        """Ping the terminal and report whether it answered."""
        server = self._require_server()
        server.send(PipeCodec.encode_int(int(MESSAGE_ID.PING)))
        answer = server.receive()
        return answer is not None
