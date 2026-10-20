"""Command line entry point.

Subcommands:

``inspect``  show what a tick container holds
``run``      replay a tick history through a strategy and write a report
``report``   re-render a report from a saved run
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mt5_backtest_sim.domain.models import Account, SymbolSpec, SymbolState
from mt5_backtest_sim.domain.value_objects import Money
from mt5_backtest_sim.execution.broker import SimulatedBroker
from mt5_backtest_sim.market_data.csv_source import CsvTickSource
from mt5_backtest_sim.market_data.ctf import CtfTickFile
from mt5_backtest_sim.market_data.feeder import TickFeeder
from mt5_backtest_sim.results.report_html import render_report
from mt5_backtest_sim.results.statistics import compute_statistics
from mt5_backtest_sim.simulation.engine import SimulationEngine
from mt5_backtest_sim.simulation.settings import SimulationSettings

__all__ = ["main"]


def _load_strategy(dotted_path: str) -> Any:
    """Import a strategy from a ``module:ClassName`` or ``module.ClassName`` reference."""
    module_name, _, attribute = dotted_path.partition(":")
    if not attribute:
        module_name, _, attribute = dotted_path.rpartition(".")
    if not module_name or not attribute:
        raise SystemExit(f"cannot parse strategy reference {dotted_path!r}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise SystemExit(f"{module_name} has no attribute {attribute!r}") from exc


def _build_symbol(spec: SymbolSpec) -> SymbolState:
    return SymbolState(spec=spec)


def _symbol_spec(args: argparse.Namespace) -> SymbolSpec:
    return SymbolSpec(
        name=args.symbol,
        base_currency=args.base_currency,
        quote_currency=args.quote_currency,
        digits=args.digits,
        point=Decimal(1).scaleb(-args.digits),
        contract_size=Decimal(args.contract_size),
    )


def _parse_moment(text: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp into UTC."""
    if not text:
        return None
    normalised = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _open_source(args: argparse.Namespace) -> Any:
    path = Path(args.ticks)
    if path.suffix.lower() == ".ctf":
        source = CtfTickFile(path, symbol=args.symbol)
        start = _parse_moment(getattr(args, "start", None))
        if start is not None:
            source.seek_to(start)
        return source
    return CsvTickSource(path, symbol=args.symbol)


def command_inspect(args: argparse.Namespace) -> int:
    """Print what a tick container holds."""
    path = Path(args.ticks)
    if path.suffix.lower() != ".ctf":
        print(f"{path} is not a .ctf container")
        return 1
    with CtfTickFile(path, symbol=args.symbol) as reader:
        header = reader.header
        print(f"file        : {path}")
        print(f"size        : {path.stat().st_size:,} bytes")
        print(f"first day   : {header.first_day} ({header.start_time:%Y-%m-%d})")
        print(f"last day    : {header.last_day} ({header.end_time:%Y-%m-%d})")
        print(f"digits      : {header.digits}")
        if args.count:
            count = 0
            for _ in reader.ticks():
                count += 1
            print(f"ticks       : {count:,}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    """Replay a tick history through a strategy."""
    spec = _symbol_spec(args)
    state = _build_symbol(spec)
    source = _open_source(args)
    feeder = TickFeeder([source])

    account = Account(
        initial_balance=Money(Decimal(args.balance)),
        balance=Money(Decimal(args.balance)),
        leverage=args.leverage,
        commission_per_lot=Decimal(args.commission),
    )
    broker = SimulatedBroker(account, {spec.name: state})
    settings = SimulationSettings(
        starting_balance=Money(Decimal(args.balance)),
        leverage=args.leverage,
        commission_per_lot=Decimal(args.commission),
        symbols=(spec.name,),
    )
    engine = SimulationEngine(broker, feeder, settings)

    strategy_class = _load_strategy(args.strategy)
    strategy = strategy_class()
    engine.attach_strategy(strategy)

    started = datetime.now(UTC)
    end = _parse_moment(getattr(args, "end", None))
    result = engine.run_all(until_time=end, close_at_end=not args.keep_open)
    elapsed = (datetime.now(UTC) - started).total_seconds()

    statistics = compute_statistics(
        result.closed_orders, result.account, result.equity_curve
    )

    print(f"state        : {result.state.value}")
    print(f"ticks        : {engine.ticks_processed:,}")
    print(f"equity points: {len(result.equity_curve):,} (sampled)")
    print(f"elapsed      : {elapsed:.1f}s")
    print(f"balance      : {result.account.balance}")
    print(f"equity       : {result.account.equity}")
    print(f"closed trades: {statistics.total_trades}")
    print(f"net profit   : {statistics.net_profit}")
    print(f"profit factor: {statistics.profit_factor if statistics.profit_factor is not None else '-'}")

    if args.json:
        payload = {
            "state": result.state.value,
            "statistics": {
                key: str(value) for key, value in statistics.as_dict().items()
            },
            "account": {
                "balance": str(result.account.balance),
                "equity": str(result.account.equity),
            },
        }
        _write(Path(args.json), json.dumps(payload, indent=2))
        print(f"json written : {args.json}")

    if args.report:
        report = render_report(
            statistics,
            result.closed_orders,
            result.operations,
            title=f"{spec.name} backtest",
        )
        _write(Path(args.report), report)
        print(f"report written: {args.report}")

    return 0


def _write(path: Path, content: str) -> None:
    """Write a file, creating the parent directory when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def command_report(args: argparse.Namespace) -> int:
    """Placeholder for rendering a report from a saved run."""
    print("report rendering from a saved run is not implemented yet")
    return 1


def build_parser() -> argparse.ArgumentParser:
    """The argument parser."""
    parser = argparse.ArgumentParser(
        prog="mt5-backtest-sim",
        description="Tick driven MetaTrader 5 style backtesting simulator",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--ticks", required=True, help="tick container (.ctf) or CSV file")
        sub.add_argument("--symbol", default="EURUSD", help="symbol name")
        sub.add_argument("--base-currency", default="EUR")
        sub.add_argument("--quote-currency", default="USD")
        sub.add_argument("--digits", type=int, default=5)
        sub.add_argument("--contract-size", default="100000")
        sub.add_argument("--start", help="start of the replay window (ISO 8601)")
        sub.add_argument("--end", help="end of the replay window (ISO 8601)")

    inspect = subparsers.add_parser("inspect", help="show what a tick container holds")
    inspect.add_argument("--ticks", required=True)
    inspect.add_argument("--symbol", default="EURUSD")
    inspect.add_argument("--count", action="store_true", help="count every tick (slow)")
    inspect.set_defaults(handler=command_inspect)

    run = subparsers.add_parser("run", help="replay a tick history through a strategy")
    add_common(run)
    run.add_argument(
        "--strategy",
        required=True,
        help="strategy reference, for example examples.ma_crossover:MovingAverageCrossover",
    )
    run.add_argument("--balance", default="10000", help="starting balance")
    run.add_argument("--leverage", type=int, default=100)
    run.add_argument("--commission", default="0", help="commission per lot")
    run.add_argument("--report", help="write an HTML report to this path")
    run.add_argument("--json", help="write a JSON summary to this path")
    run.add_argument(
        "--keep-open",
        action="store_true",
        help="do not close open positions at the end of the run",
    )
    run.set_defaults(handler=command_run)

    report = subparsers.add_parser("report", help="render a report from a saved run")
    report.add_argument("--run", required=True, help="saved run file")
    report.set_defaults(handler=command_report)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """The console script entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
