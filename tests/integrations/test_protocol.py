"""The MetaTrader 5 bridge protocol."""

from __future__ import annotations

import pytest

from mt5_backtest_sim.integrations.mt5.protocol import (
    MESSAGE_ID,
    PipeCodec,
    decode_message,
    encode_message,
)


def test_an_empty_message_carries_only_its_identifier() -> None:
    payload = encode_message(MESSAGE_ID.PING)
    assert payload == b"\x02\x00\x00\x00"
    identifier, values = decode_message(payload)
    assert identifier is MESSAGE_ID.PING
    assert values == []


def test_integers_are_little_endian() -> None:
    payload = encode_message(MESSAGE_ID.NAVIGATE_TO_TRADE, 258)
    assert payload[4:] == b"\x02\x01\x00\x00"


def test_booleans_travel_as_four_byte_integers() -> None:
    payload = encode_message(MESSAGE_ID.REVIEW_MODE, True)
    assert payload[4:] == b"\x01\x00\x00\x00"


def test_a_string_carries_its_byte_count() -> None:
    payload = encode_message(MESSAGE_ID.OPEN_CHART, "EURUSD", 60)
    length = int.from_bytes(payload[4:8], "little")
    assert length == len("EURUSD".encode("utf-16-le"))
    assert payload[8 : 8 + length].decode("utf-16-le") == "EURUSD"


def test_a_non_ascii_string_is_measured_in_bytes() -> None:
    text = "رمز"
    payload = PipeCodec.encode_string(text)
    length = int.from_bytes(payload[:4], "little")
    assert length == len(text.encode("utf-16-le"))
    assert length > len(text)


def test_a_message_round_trips() -> None:
    payload = encode_message(MESSAGE_ID.SYMBOLS, 3, True, "USD")
    identifier, values = decode_message(payload)
    assert identifier is MESSAGE_ID.SYMBOLS
    assert values[0] == 3
    assert values[1] is True
    assert values[2] == "USD"


def test_a_truncated_message_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode_message(b"\x02")


def test_an_unknown_identifier_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode_message(b"\x63\x00\x00\x00")
