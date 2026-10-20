"""Value objects for prices, volumes and money.

Prices are :class:`~decimal.Decimal`, never ``float``. A float cannot represent
``1.1000000000000001`` and rounding a price to five decimal places after a
multiplication is how a backtest silently loses a pip. Volumes are Decimal too, in
*lots* (MetaTrader 5 terminology), with the 0.01 lot step enforced at construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["Money", "Price", "Volume", "round_to_digits", "round_to_step"]

_ZERO = Decimal(0)
_LOT_STEP = Decimal("0.01")


def round_to_digits(value: Decimal, digits: int) -> Decimal:
    """Round a value to a number of decimal places, half away from zero."""
    if digits < 0:
        raise ValueError(f"digits must not be negative, got {digits}")
    quantum = Decimal(1).scaleb(-digits)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def round_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round a value to the nearest multiple of ``step``, half away from zero."""
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    steps = (value / step).quantize(_ZERO, rounding=ROUND_HALF_UP)
    return steps * step


@dataclass(frozen=True, slots=True, order=True)
class Price:
    """A market price.

    The wrapped value is a Decimal in *price units* (1.08765), not in points. The
    conversion to and from the integer point representation MetaTrader 5 uses on the
    wire lives in :meth:`to_points` and :meth:`from_points`, so the boundary conversion
    is explicit and testable rather than scattered through the engine.
    """

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"price must be a Decimal, got {type(self.value).__name__}")
        if self.value <= 0:
            raise ValueError(f"price must be positive, got {self.value}")

    @classmethod
    def from_points(cls, points: int, digits: int) -> Price:
        """Build a price from an integer number of points."""
        return cls(round_to_digits(Decimal(points).scaleb(-digits), digits))

    def to_points(self, digits: int) -> int:
        """Return the price as an integer number of points."""
        return int(round_to_digits(self.value, digits).scaleb(digits))

    def rounded(self, digits: int) -> Price:
        """Return this price rounded to ``digits`` decimal places."""
        return Price(round_to_digits(self.value, digits))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=True)
class Volume:
    """A trading volume in lots, on MetaTrader 5's 0.01 lot grid."""

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"volume must be a Decimal, got {type(self.value).__name__}")
        if self.value <= 0:
            raise ValueError(f"volume must be positive, got {self.value}")
        if self.value != round_to_step(self.value, _LOT_STEP):
            raise ValueError(f"volume must be a multiple of {_LOT_STEP} lots, got {self.value}")

    @classmethod
    def from_lots(cls, lots: float | int | str | Decimal) -> Volume:
        """Build a volume from a lot quantity."""
        return cls(round_to_step(Decimal(str(lots)), _LOT_STEP))

    @property
    def units(self) -> int:
        """The volume in hundredths of a lot, the integer MetaTrader 5 transmits."""
        return int((self.value * 100).to_integral_value())

    def scaled(self, factor: Decimal) -> Volume:
        """Return this volume multiplied by ``factor``, re-rounded to the lot grid."""
        return Volume(round_to_step(self.value * factor, _LOT_STEP))

    def __str__(self) -> str:
        return f"{self.value:.2f}"


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An amount of account currency."""

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"money must be a Decimal, got {type(self.value).__name__}")

    @classmethod
    def zero(cls) -> Money:
        """Zero money."""
        return cls(_ZERO)

    def __add__(self, other: Money) -> Money:
        return Money(self.value + other.value)

    def __sub__(self, other: Money) -> Money:
        return Money(self.value - other.value)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.value * Decimal(str(factor)))

    def __str__(self) -> str:
        return f"{self.value:.2f}"
