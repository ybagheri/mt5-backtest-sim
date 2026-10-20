"""Order lifecycle semantics.

The broker is the piece that knows how a MetaTrader 5 terminal behaves: what a market
order fills at, when a pending order triggers, which price closes a position on a stop
loss, how margin is reserved and how an OCO pair cancels its sibling.

The broker is deliberately free of any concept of *time*. It reacts to ticks and to
explicit calls from the engine; the engine owns the clock. That split is what makes the
semantics testable: every rule below can be exercised with three ticks and no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from mt5_backtest_sim.domain.enums import OperationType, OrderType, Side
from mt5_backtest_sim.domain.exceptions import (
    InsufficientFundsError,
    InvalidOrderError,
    MarginError,
    OrderNotFoundError,
    PriceError,
)
from mt5_backtest_sim.domain.models import Account, Operation, Order, SymbolState, Tick
from mt5_backtest_sim.domain.value_objects import Money, Volume
from mt5_backtest_sim.execution.ledger import OperationLedger

__all__ = ["SimulatedBroker"]


class SimulatedBroker:
    """A MetaTrader 5 style simulated broker.

    The broker owns the order book (open positions, pending orders and closed trades),
    the account and the operation ledger. The engine calls into it; it never calls
    back.
    """

    def __init__(
        self,
        account: Account,
        symbols: dict[str, SymbolState],
        ledger: OperationLedger | None = None,
    ) -> None:
        self.account = account
        self.symbols = symbols
        self.ledger = ledger if ledger is not None else OperationLedger()
        self.market_orders: list[Order] = []
        self.pending_orders: list[Order] = []
        self.closed_orders: list[Order] = []
        self._next_ticket = 1
        self._margin_cache: Money | None = None

    # ------------------------------------------------------------------ tickets

    def next_ticket(self) -> int:
        """Allocate the next ticket number."""
        ticket = self._next_ticket
        self._next_ticket += 1
        return ticket

    def find_order(self, ticket: int) -> Order:
        """Return the order with this ticket, open or pending."""
        for order in (*self.market_orders, *self.pending_orders):
            if order.ticket == ticket:
                return order
        raise OrderNotFoundError(f"no order with ticket {ticket}")

    # ------------------------------------------------------------------ helpers

    def _symbol(self, name: str) -> SymbolState:
        try:
            return self.symbols[name]
        except KeyError as exc:
            raise InvalidOrderError(f"unknown symbol {name!r}") from exc

    def _last_tick(self, name: str) -> Tick:
        state = self._symbol(name)
        if state.last_tick is None:
            raise InvalidOrderError(f"symbol {name!r} has no quote yet")
        return state.last_tick

    def _commission(self, volume: Volume) -> Money:
        return self.account.calc_commission(volume)

    def _used_margin(self) -> Money:
        """Margin locked by the open positions, summed per symbol.

        The result is cached and only recomputed when the position set changes. A
        margin calculation per symbol per tick is the difference between a backtest
        that finishes in a minute and one that finishes in an hour.
        """
        if self._margin_cache is not None:
            return self._margin_cache
        by_symbol: dict[str, Decimal] = {}
        for order in self.market_orders:
            spec = self._symbol(order.symbol).spec
            by_symbol[order.symbol] = by_symbol.get(order.symbol, Decimal(0)) + (
                spec.margin_requirement(order.volume, order.open_price, self.account.leverage)
            )
        total = sum(by_symbol.values(), Decimal(0))
        self._margin_cache = Money(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return self._margin_cache

    def _invalidate_margin(self) -> None:
        """Drop the cached margin after the position set changed."""
        self._margin_cache = None

    def _check_commission_affordable(self, commission: Money) -> None:
        if self.account.equity.value - commission.value < 0:
            raise InsufficientFundsError(
                f"equity {self.account.equity} cannot cover commission {commission}"
            )

    def _check_margin(self, volume: Volume, symbol: str) -> None:
        state = self._symbol(symbol)
        price = state.last_bid or state.last_ask
        required = state.spec.margin_requirement(volume, price, self.account.leverage)
        if required > self.account.free_margin.value:
            raise MarginError(
                f"margin {required:.2f} exceeds free margin {self.account.free_margin}"
            )

    def _check_stop_distance(self, symbol: str, distance_points: int) -> None:
        spec = self._symbol(symbol).spec
        if spec.stop_level_points and distance_points < spec.stop_level_points:
            raise PriceError(
                f"distance {distance_points} points is below the stop level "
                f"{spec.stop_level_points} points for {symbol}"
            )

    def _record(
        self,
        *,
        time: datetime,
        symbol: str,
        operation_type: OperationType,
        order_type: OrderType,
        price: Decimal,
        volume: Volume,
        ticket: int,
        profit: Money = Money.zero(),
        commission: Money = Money.zero(),
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        comment: str = "",
    ) -> Operation:
        return self.ledger.append(
            Operation(
                time=time,
                symbol=symbol,
                operation_type=operation_type,
                order_type=order_type,
                price=price,
                volume=volume,
                ticket=ticket,
                balance=self.account.balance,
                profit=profit,
                commission=commission,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=comment,
            )
        )

    # ------------------------------------------------------------- order entry

    def open_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: Volume,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        comment: str = "",
        time: datetime | None = None,
    ) -> Order:
        """Open a market position at the current quote.

        A buy fills at the ask, a sell fills at the bid. That asymmetry is not a detail:
        it is the spread, and a simulator that fills both sides at the mid price reports
        profits that no broker would ever have produced.
        """
        if order_type.is_pending:
            raise InvalidOrderError(f"{order_type} is a pending order")
        state = self._symbol(symbol)
        tick = self._last_tick(symbol)
        spec = state.spec
        volume = spec.normalise_volume(volume)
        fill_price = tick.ask if order_type.side is Side.BUY else tick.bid
        now = time or tick.time

        self._validate_levels(symbol, order_type.side, fill_price, stop_loss, take_profit)
        self._check_commission_affordable(self._commission(volume))
        self._check_margin(volume, symbol)

        order = Order(
            ticket=self.next_ticket(),
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            open_time=now,
            open_price=fill_price,
            stop_loss=stop_loss,
            initial_stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
            commission=self._commission(volume),
        )
        self.market_orders.append(order)
        self._invalidate_margin()
        self._record(
            time=now,
            symbol=symbol,
            operation_type=OperationType.OPEN_MARKET,
            order_type=order_type,
            price=fill_price,
            volume=volume,
            ticket=order.ticket,
            commission=order.commission,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
        )
        self._refresh_account()
        return order

    def place_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        price: Decimal,
        volume: Volume,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        is_oco: bool = False,
        comment: str = "",
        time: datetime | None = None,
    ) -> Order:
        """Rest an order in the book until the market reaches its trigger."""
        if not order_type.is_pending:
            raise InvalidOrderError(f"{order_type} is a market order")
        state = self._symbol(symbol)
        tick = self._last_tick(symbol)
        spec = state.spec
        volume = spec.normalise_volume(volume)
        now = time or tick.time

        self._validate_pending_price(symbol, order_type, price)
        self._validate_levels(symbol, order_type.side, price, stop_loss, take_profit)

        order = Order(
            ticket=self.next_ticket(),
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            open_time=now,
            open_price=price,
            stop_loss=stop_loss,
            initial_stop_loss=stop_loss,
            take_profit=take_profit,
            is_oco=is_oco,
            comment=comment,
            commission=self._commission(volume),
        )
        self.pending_orders.append(order)
        self._record(
            time=now,
            symbol=symbol,
            operation_type=OperationType.PLACE_PENDING,
            order_type=order_type,
            price=price,
            volume=volume,
            ticket=order.ticket,
            commission=order.commission,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
        )
        return order

    def modify_order(
        self,
        ticket: int,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        time: datetime | None = None,
    ) -> Order:
        """Change the stop loss or take profit of an open order."""
        order = self.find_order(ticket)
        tick = self._last_tick(order.symbol)
        now = time or tick.time
        self._validate_levels(
            order.symbol, order.side, order.open_price, stop_loss, take_profit
        )
        order.stop_loss = stop_loss
        order.take_profit = take_profit
        self._record(
            time=now,
            symbol=order.symbol,
            operation_type=OperationType.MODIFY,
            order_type=order.order_type,
            price=order.open_price,
            volume=order.volume,
            ticket=order.ticket,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=order.comment,
        )
        return order

    def delete_pending_order(
        self,
        ticket: int,
        reason: OperationType = OperationType.DELETE_PENDING,
        time: datetime | None = None,
    ) -> Order:
        """Remove a resting order from the book."""
        order = self.find_order(ticket)
        if not order.is_pending:
            raise InvalidOrderError(f"order {ticket} is not a pending order")
        tick = self._last_tick(order.symbol)
        now = time or tick.time
        self.pending_orders.remove(order)
        self._record(
            time=now,
            symbol=order.symbol,
            operation_type=reason,
            order_type=order.order_type,
            price=order.open_price,
            volume=order.volume,
            ticket=order.ticket,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            comment=order.comment,
        )
        return order

    # -------------------------------------------------------------- validation

    def _validate_levels(
        self,
        symbol: str,
        side: Side,
        reference_price: Decimal,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
    ) -> None:
        """Reject a stop loss or take profit that is on the wrong side of the price."""
        state = self._symbol(symbol)
        spec = state.spec
        if stop_loss is not None:
            if side is Side.BUY and stop_loss >= reference_price:
                raise PriceError(
                    f"stop loss {stop_loss} must be below {reference_price} for a buy"
                )
            if side is Side.SELL and stop_loss <= reference_price:
                raise PriceError(
                    f"stop loss {stop_loss} must be above {reference_price} for a sell"
                )
            self._check_stop_distance(
                symbol, spec.price_to_points(abs(reference_price - stop_loss))
            )
        if take_profit is not None:
            if side is Side.BUY and take_profit <= reference_price:
                raise PriceError(
                    f"take profit {take_profit} must be above {reference_price} for a buy"
                )
            if side is Side.SELL and take_profit >= reference_price:
                raise PriceError(
                    f"take profit {take_profit} must be below {reference_price} for a sell"
                )
            self._check_stop_distance(
                symbol, spec.price_to_points(abs(reference_price - take_profit))
            )

    def _validate_pending_price(self, symbol: str, order_type: OrderType, price: Decimal) -> None:
        """Reject a pending order whose trigger price is already behind the market."""
        state = self._symbol(symbol)
        tick = self._last_tick(symbol)
        if order_type == OrderType.BUY_LIMIT and price >= tick.ask:
            raise PriceError(f"buy limit {price} is not below the ask {tick.ask}")
        if order_type == OrderType.BUY_STOP and price <= tick.ask:
            raise PriceError(f"buy stop {price} is not above the ask {tick.ask}")
        if order_type == OrderType.SELL_LIMIT and price <= tick.bid:
            raise PriceError(f"sell limit {price} is not above the bid {tick.bid}")
        if order_type == OrderType.SELL_STOP and price >= tick.bid:
            raise PriceError(f"sell stop {price} is not below the bid {tick.bid}")
        spec = state.spec
        reference = tick.ask if order_type.side is Side.BUY else tick.bid
        self._check_stop_distance(symbol, spec.price_to_points(abs(reference - price)))

    # ----------------------------------------------------------------- closing

    def close_market_order(
        self,
        ticket: int,
        price: Decimal | None = None,
        operation: OperationType = OperationType.CLOSE_MARKET,
        comment: str = "",
        time: datetime | None = None,
    ) -> Order:
        """Close an open position at the current quote (or at an explicit price)."""
        order = self.find_order(ticket)
        if order.is_pending:
            raise InvalidOrderError(f"order {ticket} is pending, not open")
        tick = self._last_tick(order.symbol)
        state = self._symbol(order.symbol)
        close_price = price if price is not None else (
            tick.bid if order.is_long else tick.ask
        )
        now = time or tick.time
        profit = self._profit_of(order, state, close_price)
        order.profit_loss = profit
        order.profit_points = state.spec.price_to_points(
            (close_price - order.open_price)
            if order.is_long
            else (order.open_price - close_price)
        )
        order.balance_after = self.account.balance
        closed = order.as_closed(now, close_price, operation)
        self.market_orders.remove(order)
        self._invalidate_margin()
        self.closed_orders.append(closed)
        self.account.apply_realised(profit, order.commission, self._used_margin())
        closed.balance_after = self.account.balance
        closed.equity_after = self.account.equity
        self._record(
            time=now,
            symbol=closed.symbol,
            operation_type=operation,
            order_type=closed.order_type,
            price=close_price,
            volume=closed.volume,
            ticket=closed.ticket,
            profit=profit,
            commission=order.commission,
            comment=comment or closed.comment,
        )
        self._refresh_account()
        return closed

    def close_partially(
        self,
        ticket: int,
        volume: Volume,
        price: Decimal | None = None,
        operation: OperationType = OperationType.PARTIAL_CLOSE,
        comment: str = "",
        time: datetime | None = None,
    ) -> tuple[Order, Order]:
        """Close part of an open position.

        Returns the closed slice and the remaining position. The closed slice takes a
        new ticket and points back at the original through :attr:`Order.from_ticket`;
        the remainder keeps the original ticket.
        """
        order = self.find_order(ticket)
        if order.is_pending:
            raise InvalidOrderError(f"order {ticket} is pending, not open")
        if volume.value >= order.volume.value:
            raise InvalidOrderError(
                f"partial close volume {volume} must be smaller than {order.volume}"
            )
        tick = self._last_tick(order.symbol)
        state = self._symbol(order.symbol)
        close_price = price if price is not None else (
            tick.bid if order.is_long else tick.ask
        )
        now = time or tick.time
        closed_volume = state.spec.normalise_volume(volume)
        remaining_volume = Volume(order.volume.value - closed_volume.value)

        closed = Order(
            ticket=self.next_ticket(),
            symbol=order.symbol,
            order_type=order.order_type,
            volume=closed_volume,
            open_time=order.open_time,
            open_price=order.open_price,
            close_time=now,
            close_price=close_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            initial_stop_loss=order.initial_stop_loss,
            comment=order.comment,
            commission=self._commission(closed_volume),
            from_ticket=order.ticket,
        )
        profit = self._profit_of(closed, state, close_price)
        closed.profit_loss = profit
        closed.profit_points = state.spec.price_to_points(
            (close_price - closed.open_price)
            if closed.is_long
            else (closed.open_price - close_price)
        )
        order.volume = remaining_volume
        self._invalidate_margin()
        order.to_ticket = closed.ticket
        order.commission = self._commission(remaining_volume)

        self.closed_orders.append(closed)
        self.account.apply_realised(profit, closed.commission, self._used_margin())
        closed.balance_after = self.account.balance
        closed.equity_after = self.account.equity
        self._record(
            time=now,
            symbol=closed.symbol,
            operation_type=operation,
            order_type=closed.order_type,
            price=close_price,
            volume=closed_volume,
            ticket=closed.ticket,
            profit=profit,
            commission=closed.commission,
            comment=comment or closed.comment,
        )
        self._refresh_account()
        return closed, order

    def close_all_market_orders(self, time: datetime | None = None) -> list[Order]:
        """Close every open position at the current quote."""
        closed: list[Order] = []
        for order in list(self.market_orders):
            closed.append(
                self.close_market_order(
                    order.ticket,
                    operation=OperationType.CLOSE_AT_END,
                    comment="closed at end of run",
                    time=time,
                )
            )
        return closed

    def delete_all_pending_orders(self, time: datetime | None = None) -> list[Order]:
        """Remove every resting order."""
        removed: list[Order] = []
        for order in list(self.pending_orders):
            removed.append(
                self.delete_pending_order(
                    order.ticket,
                    reason=OperationType.DELETE_AT_END,
                    time=time,
                )
            )
        return removed

    # -------------------------------------------------------------- tick logic

    def apply_quote(self, tick: Tick) -> None:
        """Record the quote on the symbol.

        The engine calls this before the strategy runs, so a strategy that opens a
        market order fills at the price of the tick it is reacting to rather than at
        the previous one.
        """
        self._symbol(tick.symbol).update(tick)

    def process(self, tick: Tick) -> list[Operation]:
        """Process one quote.

        The order matters and mirrors what a terminal does on every tick:

        1. resting orders are checked for their trigger,
        2. open positions are checked for stop loss and take profit hits,
        3. break-even and trailing stop are applied to the survivors,
        4. the floating account is refreshed.
        """
        produced: list[Operation] = []
        produced.extend(self._process_pending(tick))
        produced.extend(self._process_positions(tick))
        self._refresh_account()
        return produced

    def on_tick(self, tick: Tick) -> list[Operation]:
        """Record the quote and process it in one step."""
        self.apply_quote(tick)
        return self.process(tick)

    def _process_pending(self, tick: Tick) -> list[Operation]:
        produced: list[Operation] = []
        for order in list(self.pending_orders):
            if order.symbol != tick.symbol:
                continue
            if order not in self.pending_orders:
                # Cancelled earlier in this same tick, for example by the OCO sibling
                # of an order that has just triggered.
                continue
            if not self._is_triggered(order, tick):
                continue
            produced.extend(self._execute_pending(order, tick))
        return produced

    def _is_triggered(self, order: Order, tick: Tick) -> bool:
        if order.order_type == OrderType.BUY_LIMIT:
            return tick.ask <= order.open_price
        if order.order_type == OrderType.BUY_STOP:
            return tick.ask >= order.open_price
        if order.order_type == OrderType.SELL_LIMIT:
            return tick.bid >= order.open_price
        if order.order_type == OrderType.SELL_STOP:
            return tick.bid <= order.open_price
        return False

    def _execute_pending(self, order: Order, tick: Tick) -> list[Operation]:
        produced: list[Operation] = []
        state = self._symbol(order.symbol)
        volume = order.volume
        commission = self._commission(volume)
        required = state.spec.margin_requirement(
            volume, order.open_price, self.account.leverage
        )
        if required > self.account.free_margin.value:
            self.pending_orders.remove(order)
            produced.append(
                self._record(
                    time=tick.time,
                    symbol=order.symbol,
                    operation_type=OperationType.DELETE_PENDING_NO_MARGIN,
                    order_type=order.order_type,
                    price=order.open_price,
                    volume=volume,
                    ticket=order.ticket,
                    comment="not enough free margin to execute",
                )
            )
            return produced
        if self.account.equity.value - commission.value < 0:
            self.pending_orders.remove(order)
            produced.append(
                self._record(
                    time=tick.time,
                    symbol=order.symbol,
                    operation_type=OperationType.DELETE_PENDING_NO_EQUITY_FOR_COMMISSION,
                    order_type=order.order_type,
                    price=order.open_price,
                    volume=volume,
                    ticket=order.ticket,
                    comment="not enough equity to pay commission",
                )
            )
            return produced

        self.pending_orders.remove(order)
        market_type = order.order_type.to_market()
        filled = Order(
            ticket=order.ticket,
            symbol=order.symbol,
            order_type=market_type,
            volume=volume,
            open_time=tick.time,
            open_price=order.open_price,
            stop_loss=order.stop_loss,
            initial_stop_loss=order.initial_stop_loss,
            take_profit=order.take_profit,
            comment=order.comment,
            is_oco=order.is_oco,
            commission=commission,
        )
        self.market_orders.append(filled)
        self._invalidate_margin()
        produced.append(
            self._record(
                time=tick.time,
                symbol=order.symbol,
                operation_type=OperationType.EXECUTE_PENDING,
                order_type=market_type,
                price=order.open_price,
                volume=volume,
                ticket=order.ticket,
                commission=commission,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                comment=order.comment,
            )
        )
        if order.is_oco:
            for sibling in list(self.pending_orders):
                if sibling.is_oco and sibling.symbol == order.symbol:
                    self.pending_orders.remove(sibling)
                    produced.append(
                        self._record(
                            time=tick.time,
                            symbol=sibling.symbol,
                            operation_type=OperationType.DELETE_PENDING_OCO,
                            order_type=sibling.order_type,
                            price=sibling.open_price,
                            volume=sibling.volume,
                            ticket=sibling.ticket,
                            comment="cancelled by the opposite order of the OCO group",
                        )
                    )
        return produced

    def _process_positions(self, tick: Tick) -> list[Operation]:
        produced: list[Operation] = []
        for order in list(self.market_orders):
            if order.symbol != tick.symbol:
                continue
            state = self._symbol(order.symbol)
            if order.stop_loss is not None and self._stop_hit(order, tick):
                before = len(self.ledger)
                self.close_market_order(
                    order.ticket,
                    price=order.stop_loss,
                    operation=OperationType.STOP_LOSS,
                    comment=f"[sl {order.stop_loss}]",
                    time=tick.time,
                )
                produced.extend(self.ledger.operations[before:])
                continue
            if order.take_profit is not None and self._take_profit_hit(order, tick):
                before = len(self.ledger)
                self.close_market_order(
                    order.ticket,
                    price=order.take_profit,
                    operation=OperationType.TAKE_PROFIT,
                    comment=f"[tp {order.take_profit}]",
                    time=tick.time,
                )
                produced.extend(self.ledger.operations[before:])
                continue
            self._apply_break_even(order, tick, state)
            self._apply_trailing_stop(order, tick, state)
            order.update_price(state.spec, tick.bid, tick.ask)
            profit = self._profit_of(order, state, tick.bid if order.is_long else tick.ask)
            order.floating_profit = profit
            self._track_excursions(order, profit)
        return produced

    def _stop_hit(self, order: Order, tick: Tick) -> bool:
        if order.stop_loss is None:
            return False
        if order.is_long:
            return tick.bid <= order.stop_loss
        return tick.ask >= order.stop_loss

    def _take_profit_hit(self, order: Order, tick: Tick) -> bool:
        if order.take_profit is None:
            return False
        if order.is_long:
            return tick.bid >= order.take_profit
        return tick.ask <= order.take_profit

    def _apply_break_even(self, order: Order, tick: Tick, state: SymbolState) -> None:
        if order.break_even_at_points <= 0:
            return
        spec = state.spec
        distance = spec.points_to_price(order.break_even_at_points)
        target = spec.points_to_price(order.break_even_move_to_points)
        if order.is_long:
            if tick.bid < order.open_price + distance:
                return
            new_stop = order.open_price + target
            if order.stop_loss is None or new_stop > order.stop_loss:
                order.stop_loss = new_stop
        else:
            if tick.ask > order.open_price - distance:
                return
            new_stop = order.open_price - target
            if order.stop_loss is None or new_stop < order.stop_loss:
                order.stop_loss = new_stop

    def _apply_trailing_stop(self, order: Order, tick: Tick, state: SymbolState) -> None:
        if order.trailing_stop_points <= 0:
            return
        spec = state.spec
        start = spec.points_to_price(order.trailing_start_points)
        distance = spec.points_to_price(order.trailing_stop_points)
        if order.is_long:
            if tick.bid < order.open_price + start:
                return
            new_stop = tick.bid - distance
            if order.stop_loss is None or new_stop > order.stop_loss:
                order.stop_loss = new_stop
        else:
            if tick.ask > order.open_price - start:
                return
            new_stop = tick.ask + distance
            if order.stop_loss is None or new_stop < order.stop_loss:
                order.stop_loss = new_stop

    def _track_excursions(self, order: Order, profit: Money) -> None:
        """Track the worst and the best excursion of an open position."""
        if profit.value < order.max_drawdown.value:
            order.max_drawdown = profit
            order.max_drawdown_points = order.profit_points
        if profit.value > order.max_favorable_excursion.value:
            order.max_favorable_excursion = profit
            order.max_favorable_excursion_points = order.profit_points

    def _profit_of(self, order: Order, state: SymbolState, price: Decimal) -> Money:
        spec = state.spec
        distance = (price - order.open_price) if order.is_long else (order.open_price - price)
        amount = distance * spec.contract_size * spec.exchange_rate * order.volume.value
        return Money(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _refresh_account(self) -> None:
        """Recompute the floating profit of the account from the open positions."""
        if not self.market_orders:
            self.account.set_floating(Money.zero(), self._used_margin())
            return
        floating = Decimal(0)
        for order in self.market_orders:
            floating += order.floating_profit.value
        self.account.set_floating(
            Money(floating.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            self._used_margin(),
        )

    def floating_profit(self) -> Money:
        """Current floating profit of the open positions."""
        return self.account.floating_profit

    def open_positions(self) -> Iterable[Order]:
        """The open positions."""
        return tuple(self.market_orders)
