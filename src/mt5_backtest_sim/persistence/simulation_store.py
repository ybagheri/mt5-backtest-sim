"""Saving and loading a simulation run.

A run is written as a gzip-compressed UTF-8 JSON document, the same container the
reference product uses for its ``.sim`` files. The document holds the account, the
operation ledger, the orders and the equity curve — everything needed to reopen a
run, inspect it, or continue it.

Money is stored as a decimal *string*, never as a JSON number: a float would quietly
lose cents, and a run that cannot be reopened with the same numbers is not worth
saving.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mt5_backtest_sim.domain.enums import OperationType, OrderType
from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.domain.models import Account, Operation, Order
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.simulation.engine import EquityPoint, SimulationResult

__all__ = ["SavedRun", "load_simulation", "save_simulation"]

SCHEMA_VERSION = 1


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _money(value: Money) -> str:
    return str(value.value)


@dataclass(frozen=True, slots=True)
class SavedRun:
    """A run read back from disk."""

    account: Account
    operations: tuple[Operation, ...]
    closed_orders: tuple[Order, ...]
    equity_curve: tuple[EquityPoint, ...]
    state: str


def _order_to_json(order: Order) -> dict[str, Any]:
    return {
        "ticket": order.ticket,
        "symbol": order.symbol,
        "order_type": order.order_type.value,
        "volume": str(order.volume.value),
        "open_time": order.open_time.isoformat(),
        "open_price": _decimal(order.open_price),
        "close_time": order.close_time.isoformat() if order.close_time else None,
        "close_price": _decimal(order.close_price),
        "stop_loss": _decimal(order.stop_loss),
        "take_profit": _decimal(order.take_profit),
        "initial_stop_loss": _decimal(order.initial_stop_loss),
        "profit_loss": _money(order.profit_loss),
        "profit_points": order.profit_points,
        "commission": _money(order.commission),
        "comment": order.comment,
        "is_oco": order.is_oco,
        "close_operation_type": (
            order.close_operation_type.value if order.close_operation_type else None
        ),
        "from_ticket": order.from_ticket,
        "to_ticket": order.to_ticket,
        "trailing_stop_points": order.trailing_stop_points,
        "trailing_start_points": order.trailing_start_points,
        "break_even_at_points": order.break_even_at_points,
        "break_even_move_to_points": order.break_even_move_to_points,
        "max_drawdown": _money(order.max_drawdown),
        "max_favorable_excursion": _money(order.max_favorable_excursion),
        "balance_after": _money(order.balance_after),
        "equity_after": _money(order.equity_after),
    }


def _order_from_json(payload: dict[str, Any]) -> Order:
    def optional_decimal(key: str) -> Decimal | None:
        value = payload.get(key)
        return None if value is None else Decimal(value)

    def optional_time(key: str) -> datetime | None:
        value = payload.get(key)
        return None if value is None else datetime.fromisoformat(value)

    order = Order(
        ticket=int(payload["ticket"]),
        symbol=str(payload["symbol"]),
        order_type=OrderType(payload["order_type"]),
        volume=Volume(Decimal(payload["volume"])),
        open_time=datetime.fromisoformat(payload["open_time"]),
        open_price=Decimal(payload["open_price"]),
        close_time=optional_time("close_time"),
        close_price=optional_decimal("close_price"),
        stop_loss=optional_decimal("stop_loss"),
        take_profit=optional_decimal("take_profit"),
        initial_stop_loss=optional_decimal("initial_stop_loss"),
        profit_loss=Money(Decimal(payload["profit_loss"])),
        profit_points=int(payload.get("profit_points", 0)),
        commission=Money(Decimal(payload.get("commission", "0"))),
        comment=str(payload.get("comment", "")),
        is_oco=bool(payload.get("is_oco", False)),
        from_ticket=payload.get("from_ticket"),
        to_ticket=payload.get("to_ticket"),
        trailing_stop_points=int(payload.get("trailing_stop_points", 0)),
        trailing_start_points=int(payload.get("trailing_start_points", 0)),
        break_even_at_points=int(payload.get("break_even_at_points", 0)),
        break_even_move_to_points=int(payload.get("break_even_move_to_points", 0)),
        max_drawdown=Money(Decimal(payload.get("max_drawdown", "0"))),
        max_favorable_excursion=Money(Decimal(payload.get("max_favorable_excursion", "0"))),
        balance_after=Money(Decimal(payload.get("balance_after", "0"))),
        equity_after=Money(Decimal(payload.get("equity_after", "0"))),
    )
    close_operation = payload.get("close_operation_type")
    if close_operation:
        order.close_operation_type = OperationType(close_operation)
    return order


def _operation_to_json(operation: Operation) -> dict[str, Any]:
    return {
        "time": operation.time.isoformat(),
        "symbol": operation.symbol,
        "operation_type": operation.operation_type.value,
        "order_type": operation.order_type.value,
        "price": _decimal(operation.price),
        "volume": str(operation.volume.value),
        "ticket": operation.ticket,
        "balance": _money(operation.balance),
        "profit": _money(operation.profit),
        "commission": _money(operation.commission),
        "stop_loss": _decimal(operation.stop_loss),
        "take_profit": _decimal(operation.take_profit),
        "comment": operation.comment,
    }


def _operation_from_json(payload: dict[str, Any]) -> Operation:
    return Operation(
        time=datetime.fromisoformat(payload["time"]),
        symbol=str(payload["symbol"]),
        operation_type=OperationType(payload["operation_type"]),
        order_type=OrderType(payload["order_type"]),
        price=Decimal(payload["price"]),
        volume=Volume(Decimal(payload["volume"])),
        ticket=int(payload["ticket"]),
        balance=Money(Decimal(payload["balance"])),
        profit=Money(Decimal(payload["profit"])),
        commission=Money(Decimal(payload.get("commission", "0"))),
        stop_loss=None
        if payload.get("stop_loss") is None
        else Decimal(payload["stop_loss"]),
        take_profit=None
        if payload.get("take_profit") is None
        else Decimal(payload["take_profit"]),
        comment=str(payload.get("comment", "")),
    )


def save_simulation(result: SimulationResult, path: str | Path) -> Path:
    """Write a run to disk and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": SCHEMA_VERSION,
        "state": result.state.value,
        "account": {
            "initial_balance": _money(result.account.initial_balance),
            "balance": _money(result.account.balance),
            "leverage": result.account.leverage,
            "commission_per_lot": str(result.account.commission_per_lot),
            "margin_warning_level": str(result.account.margin_warning_level),
        },
        "operations": [_operation_to_json(op) for op in result.operations],
        "closed_orders": [_order_to_json(order) for order in result.closed_orders],
        "equity_curve": [
            {
                "time": point.time.isoformat(),
                "balance": str(point.balance),
                "equity": str(point.equity),
            }
            for point in result.equity_curve
        ],
    }
    payload = json.dumps(document, indent=1, sort_keys=True)
    with gzip.open(target, "wb", compresslevel=1) as handle:
        handle.write(payload.encode("utf-8"))
    return target


def load_simulation(path: str | Path) -> SavedRun:
    """Read a run from disk."""
    target = Path(path)
    if not target.is_file():
        raise SimulationError(f"simulation file not found: {target}")
    try:
        with gzip.open(target, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise SimulationError(f"cannot read simulation from {target}: {exc}") from exc

    schema = document.get("schema")
    if schema != SCHEMA_VERSION:
        raise SimulationError(
            f"simulation file {target} has schema {schema}, expected {SCHEMA_VERSION}"
        )

    account_payload = document["account"]
    account = Account(
        initial_balance=Money(Decimal(account_payload["initial_balance"])),
        balance=Money(Decimal(account_payload["balance"])),
        leverage=int(account_payload.get("leverage", 100)),
        commission_per_lot=Decimal(account_payload.get("commission_per_lot", "0")),
        margin_warning_level=Decimal(account_payload.get("margin_warning_level", "30")),
    )
    operations = tuple(_operation_from_json(item) for item in document.get("operations", []))
    closed_orders = tuple(_order_from_json(item) for item in document.get("closed_orders", []))
    equity_curve = tuple(
        EquityPoint(
            time=datetime.fromisoformat(item["time"]),
            balance=Decimal(item["balance"]),
            equity=Decimal(item["equity"]),
        )
        for item in document.get("equity_curve", [])
    )
    return SavedRun(
        account=account,
        operations=operations,
        closed_orders=closed_orders,
        equity_curve=equity_curve,
        state=str(document.get("state", "STOPPED")),
    )
