"""Domain exceptions.

Every rejection the execution layer can produce is a typed exception rather than a
boolean return value. A rule such as "a stop loss may not sit inside the spread" is
worth more as a named failure than as ``False``: the caller can tell the user *why* the
order was rejected instead of showing a generic error.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "InsufficientFundsError",
    "InvalidOrderError",
    "MarginError",
    "OrderNotFoundError",
    "PriceError",
    "SimulationError",
]


class SimulationError(Exception):
    """Base class for every error this package raises."""


class InvalidOrderError(SimulationError):
    """The order request is malformed or violates a market rule."""


class OrderNotFoundError(SimulationError):
    """A ticket was referenced that no order carries."""


class PriceError(InvalidOrderError):
    """A price is outside the permitted band for the symbol."""


class MarginError(SimulationError):
    """The account cannot support the requested exposure."""


class InsufficientFundsError(MarginError):
    """The account cannot pay the commission for the requested order."""


def format_money(amount: Decimal) -> str:
    """Render a monetary amount with two decimals, for error messages."""
    return f"{amount:.2f}"
