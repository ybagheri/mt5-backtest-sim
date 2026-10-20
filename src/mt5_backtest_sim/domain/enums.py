"""Enumerations shared across layers.

All members derive from :class:`enum.StrEnum` so that they serialise to readable JSON
without a custom encoder, and so that a persisted state file survives a rename of the
Python class name.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "OperationType",
    "OrderType",
    "SimulationState",
    "SpreadType",
    "TimeZone",
    "Timeframe",
]


class Side(StrEnum):
    """Direction of a position or order.

    ``BUY`` and ``SELL`` mirror MetaTrader 5's ``ORDER_TYPE_BUY`` / ``ORDER_TYPE_SELL``.
    The naming follows MQL5 so that translating to and from the terminal is mechanical
    and auditable, rather than relying on a mapping table that can fall out of step.
    """

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        """The side that closes a position of this side."""
        return Side.SELL if self is Side.BUY else Side.BUY

    def __invert__(self) -> Side:
        return self.opposite


class OrderType(StrEnum):
    """The six order kinds the simulator supports.

    A market order fills immediately at the current quote. A pending order rests in the
    book until the price reaches its trigger, then becomes a market order.
    """

    BUY = "BUY"
    SELL = "SELL"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"

    @property
    def side(self) -> Side:
        """The side of the position this order opens."""
        if self in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP):
            return Side.BUY
        return Side.SELL

    @property
    def is_pending(self) -> bool:
        """Whether the order waits in the book for a trigger."""
        return self in (
            OrderType.BUY_LIMIT,
            OrderType.SELL_LIMIT,
            OrderType.BUY_STOP,
            OrderType.SELL_STOP,
        )

    @property
    def is_market(self) -> bool:
        """Whether the order fills immediately at the current quote."""
        return not self.is_pending

    @property
    def is_limit(self) -> bool:
        """Whether the order triggers when the market moves *towards* its price."""
        return self in (OrderType.BUY_LIMIT, OrderType.SELL_LIMIT)

    @property
    def is_stop(self) -> bool:
        """Whether the order triggers when the market moves *through* its price."""
        return self in (OrderType.BUY_STOP, OrderType.SELL_STOP)

    def to_market(self) -> OrderType:
        """The market order a pending order becomes once it triggers."""
        if self == OrderType.BUY_LIMIT or self == OrderType.BUY_STOP:
            return OrderType.BUY
        if self == OrderType.SELL_LIMIT or self == OrderType.SELL_STOP:
            return OrderType.SELL
        raise ValueError(f"{self} is already a market order")


class OperationType(StrEnum):
    """A row in the immutable operation ledger.

    The ledger is the audit trail of a run: every action the trader or the engine took
    appears exactly once, in order. Statistics and reports are derived from it rather
    than from a second, parallel bookkeeping path.
    """

    OPEN_MARKET = "OPEN_MARKET"
    PLACE_PENDING = "PLACE_PENDING"
    EXECUTE_PENDING = "EXECUTE_PENDING"
    CLOSE_MARKET = "CLOSE_MARKET"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    DELETE_PENDING = "DELETE_PENDING"
    DELETE_PENDING_OCO = "DELETE_PENDING_OCO"
    DELETE_PENDING_NO_MARGIN = "DELETE_PENDING_NO_MARGIN"
    DELETE_PENDING_NO_EQUITY_FOR_COMMISSION = "DELETE_PENDING_NO_EQUITY_FOR_COMMISSION"
    MODIFY = "MODIFY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_OUT = "STOP_OUT"
    CLOSE_AT_END = "CLOSE_AT_END"
    DELETE_AT_END = "DELETE_AT_END"
    PARTIAL_TAKE_PROFIT = "PARTIAL_TAKE_PROFIT"

    @property
    def is_opening(self) -> bool:
        """Whether the operation opened exposure."""
        return self in (
            OperationType.OPEN_MARKET,
            OperationType.PLACE_PENDING,
            OperationType.EXECUTE_PENDING,
        )

    @property
    def is_closing(self) -> bool:
        """Whether the operation reduced or ended exposure."""
        return self in (
            OperationType.CLOSE_MARKET,
            OperationType.PARTIAL_CLOSE,
            OperationType.STOP_LOSS,
            OperationType.TAKE_PROFIT,
            OperationType.STOP_OUT,
            OperationType.CLOSE_AT_END,
            OperationType.PARTIAL_TAKE_PROFIT,
        )

    @property
    def is_cancelling(self) -> bool:
        """Whether the operation removed a resting order."""
        return self in (
            OperationType.DELETE_PENDING,
            OperationType.DELETE_PENDING_OCO,
            OperationType.DELETE_PENDING_NO_MARGIN,
            OperationType.DELETE_PENDING_NO_EQUITY_FOR_COMMISSION,
            OperationType.DELETE_AT_END,
        )


class SimulationState(StrEnum):
    """Why the engine stopped."""

    RUNNING = "RUNNING"
    END_TIME_REACHED = "END_TIME_REACHED"
    NO_MORE_TICKS = "NO_MORE_TICKS"
    BANKRUPT = "BANKRUPT"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class SpreadType(StrEnum):
    """How the bid/ask spread is obtained for a symbol."""

    REAL = "REAL"
    FIXED = "FIXED"


class Timeframe(StrEnum):
    """MetaTrader 5 timeframes, in minutes.

    The value is the bar length in minutes, so ``Timeframe.M1 == 1`` and
    ``Timeframe.MONTHLY == 43200``. Keeping the number as the member value means
    seconds conversions never need a lookup table.
    """

    UNKNOWN = "0"
    M1 = "1"
    M2 = "2"
    M3 = "3"
    M4 = "4"
    M5 = "5"
    M6 = "6"
    M10 = "10"
    M12 = "12"
    M15 = "15"
    M20 = "20"
    M30 = "30"
    H1 = "60"
    H2 = "120"
    H3 = "180"
    H4 = "240"
    H6 = "360"
    H8 = "480"
    H12 = "720"
    D1 = "1440"
    W1 = "10080"
    MN1 = "43200"

    @property
    def minutes(self) -> int:
        """Bar length in minutes."""
        return int(self.value)

    @property
    def seconds(self) -> int:
        """Bar length in seconds."""
        return self.minutes * 60

    @property
    def label(self) -> str:
        """MetaTrader 5 style label, for example ``M15`` or ``H4``."""
        return _TIMEFRAME_LABELS.get(self, self.value)


_TIMEFRAME_LABELS: dict[Timeframe, str] = {
    Timeframe.M1: "M1",
    Timeframe.M2: "M2",
    Timeframe.M3: "M3",
    Timeframe.M4: "M4",
    Timeframe.M5: "M5",
    Timeframe.M6: "M6",
    Timeframe.M10: "M10",
    Timeframe.M12: "M12",
    Timeframe.M15: "M15",
    Timeframe.M20: "M20",
    Timeframe.M30: "M30",
    Timeframe.H1: "H1",
    Timeframe.H2: "H2",
    Timeframe.H3: "H3",
    Timeframe.H4: "H4",
    Timeframe.H6: "H6",
    Timeframe.H8: "H8",
    Timeframe.H12: "H12",
    Timeframe.D1: "D1",
    Timeframe.W1: "W1",
    Timeframe.MN1: "MN1",
}


class TimeZone(StrEnum):
    """Clock the simulation reports timestamps in.

    Tick data is stored in UTC. The simulator converts to the display time zone for
    chart boundaries and session logic, and keeps UTC for the ledger so that two runs
    in different time zones produce byte-identical ledgers.
    """

    UTC = "UTC"
    SERVER = "SERVER"
    LOCAL = "LOCAL"
