"""HTML report rendering.

The report is a single self-contained file: no external stylesheet, no script, no
image. It can be emailed, archived or opened from a file share and still look the same
in five years. Every number in it comes from the statistics object or the operation
ledger, never from a separate rendering-time calculation.
"""

from __future__ import annotations

import html
from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal

from mt5_backtest_sim.domain.enums import OperationType
from mt5_backtest_sim.domain.models import Operation, Order
from mt5_backtest_sim.results.statistics import Statistics

__all__ = ["render_report"]

_STYLE = """
body { font-family: Tahoma, Arial, sans-serif; font-size: 12px; color: #1a1a1a; margin: 24px; }
h1 { font-size: 18px; margin: 0 0 4px 0; }
h2 { font-size: 14px; margin: 24px 0 6px 0; border-bottom: 1px solid #d0d0d0; padding-bottom: 3px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 12px; }
th, td { border: 1px solid #dcdcdc; padding: 3px 6px; text-align: left; }
th { background: #f2f2f2; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.positive td { color: #1a7f37; }
tr.negative td { color: #b42318; }
.meta { color: #6a6a6a; font-size: 11px; }
"""


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _escape(value: object) -> str:
    return html.escape(str(value))


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    numeric: Collection[int] = (),
) -> str:
    numeric_set = set(numeric)
    head = "".join(
        f'<th class="num">{_escape(header)}</th>'
        if index in numeric_set
        else f"<th>{_escape(header)}</th>"
        for index, header in enumerate(headers)
    )
    body_parts: list[str] = []
    for row in rows:
        cells = "".join(
            f'<td class="num">{_escape(cell)}</td>'
            if index in numeric_set
            else f"<td>{_escape(cell)}</td>"
            for index, cell in enumerate(row)
        )
        body_parts.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"


def render_report(
    statistics: Statistics,
    closed_orders: Sequence[Order],
    operations: Sequence[Operation],
    *,
    title: str = "Backtest Report",
    generated_at: datetime | None = None,
) -> str:
    """Render a complete HTML report."""
    stamp = (generated_at or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M:%S")
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{_escape(title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head><body>",
        f"<h1>{_escape(title)}</h1>",
        f'<p class="meta">Generated {stamp}</p>',
    ]

    parts.append("<h2>Summary</h2>")
    summary_rows = [
        [label, value] for label, value in statistics.as_dict().items()
    ]
    parts.append(_table(["Metric", "Value"], summary_rows, numeric={1}))

    parts.append("<h2>Closed trades</h2>")
    if closed_orders:
        trade_rows = [
            [
                order.ticket,
                order.symbol,
                order.order_type.value,
                str(order.volume),
                _money(order.open_price),
                _money(order.close_price) if order.close_price is not None else "",
                str(order.close_time) if order.close_time else "",
                _money(order.profit_loss.value),
                _money(order.commission.value),
                order.close_operation_type.value if order.close_operation_type else "",
            ]
            for order in closed_orders
        ]
        parts.append(
            _table(
                [
                    "Ticket",
                    "Symbol",
                    "Type",
                    "Volume",
                    "Open",
                    "Close",
                    "Close time",
                    "Profit",
                    "Commission",
                    "Closed by",
                ],
                trade_rows,
                numeric={0, 4, 5, 7, 8},
            )
        )
    else:
        parts.append("<p>No trade was closed during this run.</p>")

    parts.append("<h2>Operations</h2>")
    if operations:
        operation_rows = [
            [
                operation.time.strftime("%Y-%m-%d %H:%M:%S"),
                operation.symbol,
                operation.operation_type.value,
                operation.order_type.value,
                str(operation.volume),
                _money(operation.price),
                _money(operation.profit.value),
                _money(operation.balance.value),
                operation.comment,
            ]
            for operation in operations
        ]
        parts.append(
            _table(
                [
                    "Time",
                    "Symbol",
                    "Operation",
                    "Order",
                    "Volume",
                    "Price",
                    "Profit",
                    "Balance",
                    "Comment",
                ],
                operation_rows,
                numeric={4, 5, 6, 7},
            )
        )
    else:
        parts.append("<p>No operation was recorded during this run.</p>")

    parts.append("</body></html>")
    return "\n".join(parts)


def count_operations(operations: Sequence[Operation], operation_type: OperationType) -> int:
    """Count the operations of one type."""
    return sum(1 for operation in operations if operation.operation_type is operation_type)
