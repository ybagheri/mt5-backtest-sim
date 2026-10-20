"""The MetaTrader 5 bridge protocol.

The simulator and the terminal exchange messages over a Windows named pipe. The
simulator owns the pipe and the expert advisor connects to it, which is the same
arrangement the reference product uses: the terminal is the host, the simulator is the
guest.

The wire format is deliberately small. Every message starts with a 32 bit identifier
and is followed by the payload fields of that message, encoded in this order:

* ``int`` â€” four bytes, little endian;
* ``bool`` â€” four bytes, ``1`` or ``0``;
* ``long`` â€” eight bytes, little endian;
* ``double`` â€” eight bytes, little endian;
* ``string`` â€” a four byte *byte* count followed by that many UTF-16LE bytes.

The byte count is the important detail: a string is measured in bytes, not in
characters, because the encoding is UTF-16 and the two differ for every non-ASCII
character.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Final

__all__ = [
    "MESSAGE_ID",
    "PipeCodec",
    "RefreshRatesMessage",
    "SymbolsMessage",
    "UpdateChartsMessage",
    "decode_message",
    "encode_message",
]


class MESSAGE_ID(IntEnum):
    """The identifier of every message on the wire.

    The numbering is part of the protocol: both sides agree on it, so a value may
    never be reused for a different meaning.
    """

    EVENTS = 0
    LOAD = 1
    PING = 2
    QUIT = 3
    REFRESH_RATES = 4
    SYMBOLS = 5
    REWIND = 6
    SAVE = 7
    UPDATE_CHARTS = 8
    UPDATE_ORDERS = 9
    OPEN_CHART = 10
    SET_VISUAL_MODE = 11
    GET_VISUAL_PRICES = 12
    GET_ATR = 13
    NAVIGATE_TO_TRADE = 14
    CHANGE_SETTINGS = 15
    REVIEW_MODE = 16


class PipeCodec:
    """Reads and writes the primitive types of the protocol."""

    @staticmethod
    def encode_int(value: int) -> bytes:
        """Encode a 32 bit signed integer."""
        return struct.pack("<i", value)

    @staticmethod
    def encode_bool(value: bool) -> bytes:
        """Encode a boolean."""
        return struct.pack("<i", 1 if value else 0)

    @staticmethod
    def encode_long(value: int) -> bytes:
        """Encode a 64 bit signed integer."""
        return struct.pack("<q", value)

    @staticmethod
    def encode_double(value: float) -> bytes:
        """Encode a double."""
        return struct.pack("<d", value)

    @staticmethod
    def encode_string(value: str) -> bytes:
        """Encode a string as a byte count followed by UTF-16LE bytes."""
        payload = value.encode("utf-16-le")
        return struct.pack("<i", len(payload)) + payload


def _encode_value(value: object) -> bytes:
    """Encode one primitive value."""
    if isinstance(value, bool):
        return PipeCodec.encode_bool(value)
    if isinstance(value, int):
        return PipeCodec.encode_int(value)
    if isinstance(value, float):
        return PipeCodec.encode_double(value)
    if isinstance(value, str):
        return PipeCodec.encode_string(value)
    raise TypeError(f"cannot encode a value of type {type(value).__name__}")


def encode_message(message_id: MESSAGE_ID, *values: object) -> bytes:
    """Encode a message with its payload.

    This is the helper for the messages whose payload is a fixed list of primitives.
    The messages with a variable payload — the rates and the charts — have their own
    classes, because their shape depends on how many symbols and bars travel with
    them.
    """
    parts: list[bytes] = [PipeCodec.encode_int(int(message_id))]
    for value in values:
        parts.append(_encode_value(value))
    return b"".join(parts)


def decode_message(payload: bytes) -> tuple[MESSAGE_ID, list[object]]:
    """Decode a message into its identifier and its payload values.

    The decoder walks the payload according to the shape of each message type, so it
    needs to know which message it is looking at. The shapes are listed in
    :data:`_MESSAGE_SHAPES`.
    """
    if len(payload) < 4:
        raise ValueError("message is too short to hold an identifier")
    (message_id,) = struct.unpack_from("<i", payload, 0)
    identifier = MESSAGE_ID(message_id)
    shape = _MESSAGE_SHAPES.get(identifier, ())
    values: list[object] = []
    offset = 4
    for kind in shape:
        value, offset = _decode_value(payload, offset, kind)
        values.append(value)
    return identifier, values


#: The payload shape of every message: a sequence of primitive type letters.
#:
#: ``i`` int, ``b`` bool, ``q`` long, ``d`` double, ``s`` string.
_MESSAGE_SHAPES: Final[dict[MESSAGE_ID, tuple[str, ...]]] = {
    MESSAGE_ID.EVENTS: ("i",),
    MESSAGE_ID.LOAD: ("i",),
    MESSAGE_ID.PING: (),
    MESSAGE_ID.QUIT: ("b",),
    MESSAGE_ID.REFRESH_RATES: ("q", "i"),
    MESSAGE_ID.SYMBOLS: ("i", "b", "s"),
    MESSAGE_ID.REWIND: ("q",),
    MESSAGE_ID.SAVE: (),
    MESSAGE_ID.UPDATE_CHARTS: ("b", "i"),
    MESSAGE_ID.UPDATE_ORDERS: ("i",),
    MESSAGE_ID.OPEN_CHART: ("s", "i"),
    MESSAGE_ID.SET_VISUAL_MODE: ("s", "i", "i", "i", "d", "d", "d"),
    MESSAGE_ID.GET_VISUAL_PRICES: ("s",),
    MESSAGE_ID.GET_ATR: ("s", "i", "i"),
    MESSAGE_ID.NAVIGATE_TO_TRADE: ("i",),
    MESSAGE_ID.CHANGE_SETTINGS: ("i", "i"),
    MESSAGE_ID.REVIEW_MODE: ("b",),
}


def _decode_value(payload: bytes, offset: int, kind: str) -> tuple[Any, int]:
    """Decode one primitive value at ``offset``."""
    if kind == "i":
        (value,) = struct.unpack_from("<i", payload, offset)
        return value, offset + 4
    if kind == "b":
        (value,) = struct.unpack_from("<i", payload, offset)
        return bool(value), offset + 4
    if kind == "q":
        (value,) = struct.unpack_from("<q", payload, offset)
        return value, offset + 8
    if kind == "d":
        (value,) = struct.unpack_from("<d", payload, offset)
        return value, offset + 8
    if kind == "s":
        (length,) = struct.unpack_from("<i", payload, offset)
        offset += 4
        text = payload[offset : offset + length].decode("utf-16-le")
        return text, offset + length
    raise ValueError(f"unknown payload type {kind!r}")


# --------------------------------------------------------------------- messages


@dataclass(slots=True)
class SymbolQuote:
    """One quote of one symbol."""

    symbol: str
    bid: float
    ask: float


@dataclass(slots=True)
class SymbolDescription:
    """The contract description of one symbol."""

    symbol: str
    base_currency: str
    quote_currency: str
    digits: int
    pip_size_points: int
    lot_size: int


@dataclass(slots=True)
class Bar:
    """One bar of a chart."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class RefreshRatesMessage:
    """The simulator tells the terminal what the market is doing now.

    Payload: the simulation time, then one quote per symbol.
    """

    __slots__ = ("quotes", "time")

    def __init__(self, time: int, quotes: list[SymbolQuote]) -> None:
        self.time = time
        self.quotes = quotes

    def encode(self) -> bytes:
        """Encode the message."""
        parts = [PipeCodec.encode_long(self.time), PipeCodec.encode_int(len(self.quotes))]
        for quote in self.quotes:
            parts.append(PipeCodec.encode_string(quote.symbol))
            parts.append(PipeCodec.encode_double(quote.bid))
            parts.append(PipeCodec.encode_double(quote.ask))
        return b"".join(parts)

    @classmethod
    def decode(cls, payload: bytes) -> RefreshRatesMessage:
        """Decode the message."""
        time_value = int(struct.unpack_from("<q", payload, 0)[0])
        count = int(struct.unpack_from("<i", payload, 8)[0])
        offset = 12
        quotes: list[SymbolQuote] = []
        for _ in range(count):
            symbol, offset = _decode_value(payload, offset, "s")
            bid, offset = _decode_value(payload, offset, "d")
            ask, offset = _decode_value(payload, offset, "d")
            quotes.append(SymbolQuote(str(symbol), float(bid), float(ask)))
        return cls(time_value, quotes)


class SymbolsMessage:
    """The simulator tells the terminal which symbols the run covers."""

    __slots__ = ("account_currency", "rewinding_enabled", "symbols")

    def __init__(
        self,
        symbols: list[SymbolDescription],
        rewinding_enabled: bool = True,
        account_currency: str = "USD",
    ) -> None:
        self.symbols = symbols
        self.rewinding_enabled = rewinding_enabled
        self.account_currency = account_currency

    def encode(self) -> bytes:
        """Encode the message."""
        parts = [
            PipeCodec.encode_int(len(self.symbols)),
            PipeCodec.encode_bool(self.rewinding_enabled),
            PipeCodec.encode_string(self.account_currency),
        ]
        for symbol in self.symbols:
            parts.append(PipeCodec.encode_string(symbol.symbol))
            parts.append(PipeCodec.encode_string(symbol.base_currency))
            parts.append(PipeCodec.encode_string(symbol.quote_currency))
            parts.append(PipeCodec.encode_int(symbol.digits))
            parts.append(PipeCodec.encode_int(symbol.pip_size_points))
            parts.append(PipeCodec.encode_int(symbol.lot_size))
        return b"".join(parts)

    @classmethod
    def decode(cls, payload: bytes) -> SymbolsMessage:
        """Decode the message."""
        count = int(struct.unpack_from("<i", payload, 0)[0])
        rewinding = bool(struct.unpack_from("<i", payload, 4)[0])
        currency, offset = _decode_value(payload, 8, "s")
        symbols: list[SymbolDescription] = []
        for _ in range(count):
            name, offset = _decode_value(payload, offset, "s")
            base, offset = _decode_value(payload, offset, "s")
            quote, offset = _decode_value(payload, offset, "s")
            digits, offset = _decode_value(payload, offset, "i")
            pip_points, offset = _decode_value(payload, offset, "i")
            lot_size, offset = _decode_value(payload, offset, "i")
            symbols.append(
                SymbolDescription(
                    str(name),
                    str(base),
                    str(quote),
                    int(digits),
                    int(pip_points),
                    int(lot_size),
                )
            )
        return cls(symbols, rewinding, str(currency))


class UpdateChartsMessage:
    """The simulator sends the bars it built to the terminal."""

    __slots__ = ("bars", "symbol", "timeframe")

    def __init__(self, symbol: str, timeframe: int, bars: list[Bar]) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.bars = bars

    def encode(self) -> bytes:
        """Encode the message."""
        parts = [
            PipeCodec.encode_string(self.symbol),
            PipeCodec.encode_int(self.timeframe),
            PipeCodec.encode_int(len(self.bars)),
        ]
        for bar in self.bars:
            parts.append(PipeCodec.encode_long(bar.time))
            parts.append(PipeCodec.encode_double(bar.open))
            parts.append(PipeCodec.encode_double(bar.high))
            parts.append(PipeCodec.encode_double(bar.low))
            parts.append(PipeCodec.encode_double(bar.close))
            parts.append(PipeCodec.encode_int(bar.volume))
        return b"".join(parts)

    @classmethod
    def decode(cls, payload: bytes) -> UpdateChartsMessage:
        """Decode the message."""
        symbol, offset = _decode_value(payload, 0, "s")
        timeframe, offset = _decode_value(payload, offset, "i")
        count, offset = _decode_value(payload, offset, "i")
        bars: list[Bar] = []
        for _ in range(count):
            time_value, offset = _decode_value(payload, offset, "q")
            open_value, offset = _decode_value(payload, offset, "d")
            high, offset = _decode_value(payload, offset, "d")
            low, offset = _decode_value(payload, offset, "d")
            close, offset = _decode_value(payload, offset, "d")
            volume, offset = _decode_value(payload, offset, "i")
            bars.append(
                Bar(
                    int(time_value),
                    float(open_value),
                    float(high),
                    float(low),
                    float(close),
                    int(volume),
                )
            )
        return cls(str(symbol), int(timeframe), bars)
