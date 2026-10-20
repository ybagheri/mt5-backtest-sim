"""The operation ledger.

The ledger is the audit trail of a run: an append-only list of immutable
:class:`~mt5_backtest_sim.domain.models.Operation` rows. Statistics, reports and the
trade blotter are all derived from it, which means a number in a report can always be
traced back to the action that produced it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime

from mt5_backtest_sim.domain.enums import OperationType
from mt5_backtest_sim.domain.models import Operation

__all__ = ["OperationLedger"]


class OperationLedger:
    """Append-only sequence of operations."""

    def __init__(self) -> None:
        self._operations: list[Operation] = []

    def __len__(self) -> int:
        return len(self._operations)

    def __iter__(self) -> Iterator[Operation]:
        return iter(self._operations)

    def append(self, operation: Operation) -> Operation:
        """Append one operation and return it."""
        self._operations.append(operation)
        return operation

    def extend(self, operations: Iterable[Operation]) -> None:
        """Append several operations."""
        self._operations.extend(operations)

    @property
    def operations(self) -> tuple[Operation, ...]:
        """Every operation, in order."""
        return tuple(self._operations)

    def of_type(self, *types: OperationType) -> tuple[Operation, ...]:
        """Every operation whose type is one of ``types``."""
        wanted = set(types)
        return tuple(op for op in self._operations if op.operation_type in wanted)

    def between(self, start: datetime, end: datetime) -> tuple[Operation, ...]:
        """Every operation whose timestamp falls in ``[start, end]``."""
        return tuple(op for op in self._operations if start <= op.time <= end)

    def after(self, moment: datetime) -> tuple[Operation, ...]:
        """Every operation strictly after ``moment``."""
        return tuple(op for op in self._operations if op.time > moment)

    def truncate_after(self, moment: datetime) -> int:
        """Drop every operation after ``moment`` and report how many were removed.

        This is what a rewind does: the ledger is rewound to a point in time exactly as
        the order book is.
        """
        keep = [op for op in self._operations if op.time <= moment]
        removed = len(self._operations) - len(keep)
        self._operations = keep
        return removed

    def clear(self) -> None:
        """Drop every operation."""
        self._operations.clear()
