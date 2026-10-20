"""Domain models.

The models are the vocabulary of the simulator: symbols, ticks, bars, orders, the
account and the operation ledger.

Two rules shape this module.

*Prices are exact.* Every price, level and money amount is a
:class:`~decimal.Decimal`. The engine converts to the integer point representation only
at the edges of the system (the MT5 pipe, the report writer).

*The ledger is the truth.* Every state change the engine performs appends exactly one
immutable :class:`Operation` row. Statistics, reports and the trade blotter are derived
from that ledger rather than from a second bookkeeping path, so a number that appears in
a report can always be traced back to the action that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from mt5_backtest_sim.domain.enums import OperationType, OrderType, Side, SpreadType
from mt5_backtest_sim.domain.value_objects import Money, Volume, round_to_step

__all__ = [
    "Account",
    "Candle",
    "Operation",
    "Order",
    "SymbolSpec",
    "SymbolState",
    "Tick",
    "to_utc",
    "utc_now",
]


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """Static description of a tradable instrument.

    Everything here is contract metadata: it never changes during a run, and it is
    exactly what a MetaTrader 5 terminal would report for the same symbol.
    """

    name: str
    base_currency: str
    quote_currency: str
    digits: int = 5
    point: Decimal = Decimal("0.00001")
    contract_size: Decimal = Decimal("100000")
    min_volume: Volume = field(default_factory=lambda: Volume(Decimal("0.01")))
    max_volume: Volume = field(default_factory=lambda: Volume(Decimal("100")))
    volume_step: Decimal = Decimal("0.01")
    stop_level_points: int = 0
    spread_type: SpreadType = SpreadType.REAL
    fixed_spread_points: int = 0
    pip_points: int = 10
    exchange_rate: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("symbol name must not be empty")
        if self.digits < 0:
            raise ValueError(f"digits must not be negative, got {self.digits}")
        if self.point <= 0:
            raise ValueError(f"point must be positive, got {self.point}")
        if self.contract_size <= 0:
            raise ValueError(f"contract_size must be positive, got {self.contract_size}")
        if self.volume_step <= 0:
            raise ValueError(f"volume_step must be positive, got {self.volume_step}")
        if self.pip_points <= 0:
            raise ValueError(f"pip_points must be positive, got {self.pip_points}")

    @property
    def pip_size(self) -> Decimal:
        """Price distance of one pip."""
        return self.point * self.pip_points

    @property
    def tick_size(self) -> Decimal:
        """Price distance of one point, the smallest tradable increment."""
        return self.point

    def points_to_price(self, points: int) -> Decimal:
        """Convert a distance in points to a price distance."""
        return self.point * points

    def price_to_points(self, distance: Decimal) -> int:
        """Convert a price distance to points, rounded to the nearest whole point."""
        return int((distance / self.point).to_integral_value())

    def normalise_volume(self, volume: Volume) -> Volume:
        """Clamp a volume to the symbol's allowed range and lot step."""
        lots = round_to_step(volume.value, self.volume_step)
        if lots < self.min_volume.value:
            lots = self.min_volume.value
        if lots > self.max_volume.value:
            lots = self.max_volume.value
        return Volume(lots)

    def point_value(self, price: Decimal) -> Decimal:
        """Account-currency value of one point for one lot."""
        return self.contract_size * price * self.exchange_rate

    def margin_requirement(self, volume: Volume, price: Decimal, leverage: int) -> Decimal:
        """Margin needed to hold ``volume`` at ``price`` at the given leverage."""
        if leverage <= 0:
            raise ValueError(f"leverage must be positive, got {leverage}")
        return volume.value * self.contract_size * price * self.exchange_rate / Decimal(leverage)

    def volume_for_risk(
        self,
        distance: Decimal,
        risk_amount: Decimal,
        price: Decimal,
    ) -> Volume:
        """Volume that risks ``risk_amount`` over a price ``distance``."""
        if distance <= 0:
            raise ValueError(f"distance must be positive, got {distance}")
        if risk_amount <= 0:
            raise ValueError(f"risk amount must be positive, got {risk_amount}")
        per_lot = distance * self.point_value(price)
        return self.normalise_volume(Volume.from_lots(risk_amount / per_lot))


@dataclass(frozen=True, slots=True)
class Tick:
    """One bid/ask quote at a point in time.

    ``time`` is timezone-aware UTC. The simulator converts to the display time zone
    only where a human reads a timestamp; the ledger always stores UTC so that two runs
    in different time zones produce identical ledgers.
    """

    time: datetime
    symbol: str
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.time.tzinfo is None or self.time.tzinfo.utcoffset(self.time) is None:
            raise ValueError(f"tick time must be timezone-aware, got {self.time!r}")
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(f"tick prices must be positive, got bid={self.bid} ask={self.ask}")
        if self.ask < self.bid:
            raise ValueError(f"ask {self.ask} is below bid {self.bid}")

    @property
    def mid(self) -> Decimal:
        """The mid price between bid and ask."""
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        """The bid/ask distance."""
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLC bar built from ticks.

    ``open_time`` is the bar's **open** timestamp. A bar is closed once the next bar's
    open time has been reached; the engine never feeds a partially formed bar to a
    strategy, which is what keeps look-ahead bias out of the results.
    """

    open_time: datetime
    timeframe_seconds: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)
    tick_volume: int = 0

    def __post_init__(self) -> None:
        if self.timeframe_seconds <= 0:
            raise ValueError(
                f"timeframe_seconds must be positive, got {self.timeframe_seconds}"
            )
        if self.high < self.low:
            raise ValueError(f"high {self.high} is below low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open {self.open} is outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close {self.close} is outside [{self.low}, {self.high}]")


@dataclass(slots=True)
class SymbolState:
    """The live state of one symbol during a run."""

    spec: SymbolSpec
    last_tick: Tick | None = None
    last_bid: Decimal = Decimal(0)
    last_ask: Decimal = Decimal(0)

    def update(self, tick: Tick) -> None:
        """Apply a tick to this symbol."""
        self.last_tick = tick
        self.last_bid = tick.bid
        self.last_ask = tick.ask

    def rewind(self) -> None:
        """Forget the current quote, used when the engine jumps back in time."""
        self.last_tick = None
        self.last_bid = Decimal(0)
        self.last_ask = Decimal(0)

    @property
    def spread_points(self) -> int:
        """Current spread in points."""
        if self.spec.spread_type is SpreadType.FIXED:
            return self.spec.fixed_spread_points
        if self.last_tick is None:
            return 0
        return self.spec.price_to_points(self.last_tick.spread)

    @property
    def point_value(self) -> Decimal:
        """Account-currency value of one point for one lot at the current price."""
        price = self.last_bid or self.last_ask
        return self.spec.point_value(price)


@dataclass(slots=True)
class Order:
    """One order or position.

    A single class models both the resting order and the resulting position, which is
    how MetaTrader 5 itself presents it to the user: a market order is a position, and
    a pending order is a position that has not opened yet. The distinction is carried
    by :attr:`order_type`.

    This is the one deliberately mutable aggregate in the domain. A position's price,
    profit and stop levels change on every tick, and copying the order book on each
    tick would be both slower and less honest than mutating in place. Everything that
    *replaces* an order (a partial close, a rewind) returns a new instance.
    """

    ticket: int
    symbol: str
    order_type: OrderType
    volume: Volume
    open_time: datetime
    open_price: Decimal
    close_time: datetime | None = None
    close_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    initial_stop_loss: Decimal | None = None
    profit_loss: Money = field(default_factory=Money.zero)
    profit_points: int = 0
    commission: Money = field(default_factory=Money.zero)
    comment: str = ""
    is_oco: bool = False
    close_operation_type: OperationType | None = None
    from_ticket: int | None = None
    to_ticket: int | None = None
    trailing_stop_points: int = 0
    trailing_start_points: int = 0
    break_even_at_points: int = 0
    break_even_move_to_points: int = 0
    max_drawdown: Money = field(default_factory=Money.zero)
    max_drawdown_points: int = 0
    max_favorable_excursion: Money = field(default_factory=Money.zero)
    max_favorable_excursion_points: int = 0
    balance_after: Money = field(default_factory=Money.zero)
    equity_after: Money = field(default_factory=Money.zero)
    floating_profit: Money = field(default_factory=Money.zero)
    side: Side = field(init=False)
    is_long: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.ticket <= 0:
            raise ValueError(f"ticket must be positive, got {self.ticket}")
        if self.open_price <= 0:
            raise ValueError(f"open_price must be positive, got {self.open_price}")
        # The side and its boolean form are read on every tick of every position, so
        # they are resolved once here rather than through three property calls.
        self.side = self.order_type.side
        self.is_long = self.side is Side.BUY

    @property
    def is_pending(self) -> bool:
        """Whether the order is still resting in the book."""
        return self.order_type.is_pending

    @property
    def is_market(self) -> bool:
        """Whether the order is a market order or an opened position."""
        return self.order_type.is_market

    @property
    def is_closed(self) -> bool:
        """Whether the position has been closed."""
        return self.close_time is not None

    def update_price(self, spec: SymbolSpec, bid: Decimal, ask: Decimal) -> int:
        """Recompute the floating profit from the current quote.

        Returns the profit in points. A long position is valued at the bid, a short
        position at the ask, exactly as a real broker values it.
        """
        price = bid if self.is_long else ask
        distance = (price - self.open_price) if self.is_long else (self.open_price - price)
        self.profit_points = spec.price_to_points(distance)
        return self.profit_points

    def as_closed(
        self,
        close_time: datetime,
        close_price: Decimal,
        operation: OperationType,
    ) -> Order:
        """Return a closed copy of this order."""
        return Order(
            ticket=self.ticket,
            symbol=self.symbol,
            order_type=self.order_type,
            volume=self.volume,
            open_time=self.open_time,
            open_price=self.open_price,
            close_time=close_time,
            close_price=close_price,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            initial_stop_loss=self.initial_stop_loss,
            profit_loss=self.profit_loss,
            profit_points=self.profit_points,
            commission=self.commission,
            comment=self.comment,
            is_oco=self.is_oco,
            close_operation_type=operation,
            from_ticket=self.from_ticket,
            to_ticket=self.to_ticket,
            trailing_stop_points=self.trailing_stop_points,
            trailing_start_points=self.trailing_start_points,
            break_even_at_points=self.break_even_at_points,
            break_even_move_to_points=self.break_even_move_to_points,
            max_drawdown=self.max_drawdown,
            max_drawdown_points=self.max_drawdown_points,
            max_favorable_excursion=self.max_favorable_excursion,
            max_favorable_excursion_points=self.max_favorable_excursion_points,
            balance_after=self.balance_after,
            equity_after=self.equity_after,
        )


@dataclass(frozen=True, slots=True)
class Operation:
    """One immutable row of the audit trail."""

    time: datetime
    symbol: str
    operation_type: OperationType
    order_type: OrderType
    price: Decimal
    volume: Volume
    ticket: int
    balance: Money
    profit: Money
    commission: Money = field(default_factory=Money.zero)
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    comment: str = ""

    def __post_init__(self) -> None:
        if self.time.tzinfo is None or self.time.tzinfo.utcoffset(self.time) is None:
            raise ValueError(f"operation time must be timezone-aware, got {self.time!r}")


@dataclass(slots=True)
class Account:
    """The simulated trading account.

    ``balance`` is realised money, ``equity`` is balance plus floating profit. The
    distinction is the whole point of the account: a position that is open and losing
    reduces equity immediately but only touches balance when it closes.
    """

    initial_balance: Money
    balance: Money
    leverage: int = 100
    commission_per_lot: Decimal = Decimal(0)
    margin_warning_level: Decimal = Decimal(30)
    floating_profit: Money = field(default_factory=Money.zero)
    used_margin: Money = field(default_factory=Money.zero)
    paid_commissions: Money = field(default_factory=Money.zero)

    def __post_init__(self) -> None:
        if self.leverage <= 0:
            raise ValueError(f"leverage must be positive, got {self.leverage}")
        if self.initial_balance.value < 0:
            raise ValueError(f"initial_balance must not be negative, got {self.initial_balance}")

    @property
    def equity(self) -> Money:
        """Balance plus floating profit of the open positions."""
        return Money(self.balance.value + self.floating_profit.value)

    @property
    def profit(self) -> Money:
        """Floating profit of the open positions."""
        return self.floating_profit

    @property
    def free_margin(self) -> Money:
        """Margin still available."""
        return Money(self.equity.value - self.used_margin.value)

    def apply_realised(
        self,
        profit: Money,
        commission: Money,
        used_margin: Money,
    ) -> None:
        """Book a closed trade: move profit and commission into the balance."""
        self.balance = Money(self.balance.value + profit.value - commission.value)
        self.paid_commissions = Money(self.paid_commissions.value + commission.value)
        self.used_margin = used_margin

    def set_floating(self, floating_profit: Money, used_margin: Money) -> None:
        """Update the floating state of the account without touching the balance."""
        self.floating_profit = floating_profit
        self.used_margin = used_margin

    def margin_ratio(self) -> Decimal:
        """Used margin as a percentage of equity."""
        equity = self.equity.value
        if equity <= 0:
            return Decimal(0)
        return self.used_margin.value / equity * Decimal(100)

    def is_margin_warning(self) -> bool:
        """Whether the account has crossed its margin warning level."""
        return self.margin_ratio() < self.margin_warning_level

    def is_bankrupt(self) -> bool:
        """Whether the account has no equity left."""
        return self.equity.value <= 0

    def calc_commission(self, volume: Volume) -> Money:
        """Commission charged for a trade of the given volume."""
        if self.commission_per_lot <= 0:
            return Money.zero()
        return Money((volume.value * self.commission_per_lot).quantize(Decimal("0.01")))


def utc_now() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Convert a datetime to UTC, assuming UTC when it is naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
