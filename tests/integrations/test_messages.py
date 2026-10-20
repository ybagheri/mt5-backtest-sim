"""The typed messages of the bridge protocol."""

from __future__ import annotations

import struct

from mt5_backtest_sim.integrations.mt5.protocol import (
    Bar,
    RefreshRatesMessage,
    SymbolDescription,
    SymbolQuote,
    SymbolsMessage,
    UpdateChartsMessage,
)


def test_a_rate_message_round_trips() -> None:
    message = RefreshRatesMessage(
        1704153600,
        [SymbolQuote("EURUSD", 1.10025, 1.10035), SymbolQuote("GBPUSD", 1.25010, 1.25020)],
    )
    restored = RefreshRatesMessage.decode(message.encode())

    assert restored.time == 1704153600
    assert len(restored.quotes) == 2
    assert restored.quotes[0].symbol == "EURUSD"
    assert abs(restored.quotes[0].bid - 1.10025) < 1e-9
    assert abs(restored.quotes[0].ask - 1.10035) < 1e-9
    assert restored.quotes[1].symbol == "GBPUSD"


def test_a_symbols_message_round_trips() -> None:
    message = SymbolsMessage(
        [
            SymbolDescription("EURUSD", "EUR", "USD", 5, 10, 100000),
            SymbolDescription("USDJPY", "USD", "JPY", 3, 10, 100000),
        ],
        rewinding_enabled=False,
        account_currency="EUR",
    )
    restored = SymbolsMessage.decode(message.encode())

    assert restored.rewinding_enabled is False
    assert restored.account_currency == "EUR"
    assert len(restored.symbols) == 2
    assert restored.symbols[1].digits == 3
    assert restored.symbols[1].quote_currency == "JPY"


def test_a_chart_message_round_trips() -> None:
    message = UpdateChartsMessage(
        "EURUSD",
        1,
        [
            Bar(1704153600, 1.10000, 1.10100, 1.09900, 1.10050, 42),
            Bar(1704153660, 1.10050, 1.10200, 1.10010, 1.10180, 37),
        ],
    )
    restored = UpdateChartsMessage.decode(message.encode())

    assert restored.symbol == "EURUSD"
    assert restored.timeframe == 1
    assert len(restored.bars) == 2
    assert restored.bars[0].volume == 42
    assert abs(restored.bars[1].close - 1.10180) < 1e-9


def test_a_rate_message_with_no_quotes_is_valid() -> None:
    restored = RefreshRatesMessage.decode(RefreshRatesMessage(1, []).encode())
    assert restored.quotes == []
    assert restored.time == 1


def test_the_time_of_a_message_is_a_unix_timestamp() -> None:
    payload = RefreshRatesMessage(1704153600, []).encode()
    assert struct.unpack_from("<q", payload, 0)[0] == 1704153600
