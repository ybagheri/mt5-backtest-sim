"""Run statistics.

Everything here is derived from the closed orders and the equity curve. Nothing is
accumulated during the run: a statistic is a pure function of the finished run, so it
can be recomputed at any time — after a rewind, after loading a saved simulation, or
after a report is regenerated — and it will always agree with itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from mt5_backtest_sim.domain.models import Account, Order
from mt5_backtest_sim.domain.value_objects import Money
from mt5_backtest_sim.simulation.engine import EquityPoint

__all__ = ["Statistics", "compute_statistics"]

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Statistics:
    """The summary numbers of a finished run."""

    total_trades: int
    long_trades: int
    short_trades: int
    win_trades: int
    loss_trades: int
    gross_profit: Money
    gross_loss: Money
    net_profit: Money
    profit_factor: Decimal | None
    expected_payoff: Money
    recovery_factor: Decimal | None
    max_balance: Money
    absolute_drawdown: Money
    absolute_drawdown_percent: Decimal
    max_money_drawdown: Money
    max_money_drawdown_percent: Decimal
    max_relative_drawdown: Decimal
    max_relative_drawdown_money: Money
    largest_profit_trade: Money
    largest_loss_trade: Money
    average_profit_trade: Money
    average_loss_trade: Money
    max_consecutive_wins: int
    max_consecutive_wins_amount: Money
    max_consecutive_losses: int
    max_consecutive_losses_amount: Money
    total_commission: Money

    def as_dict(self) -> dict[str, str]:
        """The statistics as a flat mapping of readable labels to values."""
        return {
            "Total trades": str(self.total_trades),
            "Long trades": str(self.long_trades),
            "Short trades": str(self.short_trades),
            "Profit trades": str(self.win_trades),
            "Loss trades": str(self.loss_trades),
            "Gross profit": str(self.gross_profit),
            "Gross loss": str(self.gross_loss),
            "Total net profit": str(self.net_profit),
            "Profit factor": _ratio_text(self.profit_factor),
            "Expected payoff": str(self.expected_payoff),
            "Recovery factor": _ratio_text(self.recovery_factor),
            "Maximum balance": str(self.max_balance),
            "Absolute drawdown": str(self.absolute_drawdown),
            "Maximum drawdown (money)": str(self.max_money_drawdown),
            "Maximum drawdown (percent)": f"{self.max_money_drawdown_percent:.2f}%",
            "Maximum relative drawdown": f"{self.max_relative_drawdown:.2f}%",
            "Largest profit trade": str(self.largest_profit_trade),
            "Largest loss trade": str(self.largest_loss_trade),
            "Average profit trade": str(self.average_profit_trade),
            "Average loss trade": str(self.average_loss_trade),
            "Maximum consecutive wins": str(self.max_consecutive_wins),
            "Maximum consecutive wins (amount)": str(self.max_consecutive_wins_amount),
            "Maximum consecutive losses": str(self.max_consecutive_losses),
            "Maximum consecutive losses (amount)": str(self.max_consecutive_losses_amount),
            "Paid commissions": str(self.total_commission),
        }


def _ratio_text(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def compute_statistics(
    closed_orders: Sequence[Order],
    account: Account,
    equity_curve: Sequence[EquityPoint] = (),
) -> Statistics:
    """Compute the statistics of a finished run."""
    wins = [order for order in closed_orders if order.profit_loss.value > 0]
    losses = [order for order in closed_orders if order.profit_loss.value < 0]
    gross_profit = sum((order.profit_loss.value for order in wins), _ZERO)
    gross_loss = sum((-order.profit_loss.value for order in losses), _ZERO)
    net_profit = gross_profit - gross_loss

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expected_payoff = (
        Money(net_profit / len(closed_orders)) if closed_orders else Money.zero()
    )

    equity_values = [point.equity for point in equity_curve]
    balance_values = [point.balance for point in equity_curve]
    max_balance = max(balance_values, default=account.initial_balance.value)

    absolute_drawdown = max(
        (account.initial_balance.value - value for value in equity_values), default=_ZERO
    )
    absolute_drawdown_percent = (
        (absolute_drawdown / account.initial_balance.value * Decimal(100))
        if account.initial_balance.value > 0
        else _ZERO
    )

    peak = _ZERO
    max_money_drawdown = _ZERO
    max_money_drawdown_percent = _ZERO
    for value in equity_values:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = peak - value
            if drawdown > max_money_drawdown:
                max_money_drawdown = drawdown
                max_money_drawdown_percent = drawdown / peak * Decimal(100)

    relative_peak = _ZERO
    max_relative_drawdown = _ZERO
    max_relative_drawdown_money = _ZERO
    for value in equity_values:
        if value > relative_peak:
            relative_peak = value
        if relative_peak > 0:
            drawdown_percent = (relative_peak - value) / relative_peak * Decimal(100)
            if drawdown_percent > max_relative_drawdown:
                max_relative_drawdown = drawdown_percent
                max_relative_drawdown_money = relative_peak - value

    recovery_factor = (
        (gross_profit + gross_loss) / max_money_drawdown if max_money_drawdown > 0 else None
    )

    longest_wins, win_amount = _longest_run(wins)
    longest_losses, loss_amount = _longest_run(losses)

    commission = sum((order.commission.value for order in closed_orders), _ZERO)

    return Statistics(
        total_trades=len(closed_orders),
        long_trades=sum(1 for order in closed_orders if order.is_long),
        short_trades=sum(1 for order in closed_orders if not order.is_long),
        win_trades=len(wins),
        loss_trades=len(losses),
        gross_profit=Money(gross_profit),
        gross_loss=Money(gross_loss),
        net_profit=Money(net_profit),
        profit_factor=profit_factor,
        expected_payoff=expected_payoff,
        recovery_factor=recovery_factor,
        max_balance=Money(max_balance),
        absolute_drawdown=Money(absolute_drawdown),
        absolute_drawdown_percent=absolute_drawdown_percent,
        max_money_drawdown=Money(max_money_drawdown),
        max_money_drawdown_percent=max_money_drawdown_percent,
        max_relative_drawdown=max_relative_drawdown,
        max_relative_drawdown_money=Money(max_relative_drawdown_money),
        largest_profit_trade=(
            Money(max((order.profit_loss.value for order in wins), default=_ZERO))
        ),
        largest_loss_trade=(
            Money(min((order.profit_loss.value for order in losses), default=_ZERO))
        ),
        average_profit_trade=(
            Money(gross_profit / len(wins)) if wins else Money.zero()
        ),
        average_loss_trade=(
            Money(gross_loss / len(losses)) if losses else Money.zero()
        ),
        max_consecutive_wins=longest_wins,
        max_consecutive_wins_amount=win_amount,
        max_consecutive_losses=longest_losses,
        max_consecutive_losses_amount=loss_amount,
        total_commission=Money(commission),
    )


def _longest_run(subject: Sequence[Order]) -> tuple[int, Money]:
    """The longest run of consecutive profitable trades and the amount it made."""
    longest = 0
    longest_amount = _ZERO
    current_amount = _ZERO
    for index, order in enumerate(subject, start=1):
        if order.profit_loss.value <= 0:
            break
        current_amount += order.profit_loss.value
        if index > longest:
            longest = index
            longest_amount = current_amount
    return longest, Money(longest_amount)
