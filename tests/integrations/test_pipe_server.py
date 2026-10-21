"""The Windows named pipe server, tested against a real client."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Final

import pytest

from mt5_backtest_sim.domain.exceptions import SimulationError
from mt5_backtest_sim.integrations.mt5.pipe_server import PipeServer, pipe_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named pipes need Windows")

GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
OPEN_EXISTING: Final = 3
INVALID_HANDLE_VALUE: Final = -1
ERROR_PIPE_BUSY: Final = 231


class PipeClient:
    """A minimal named pipe client, used to exercise the server."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._kernel32.CreateFileW.restype = ctypes.c_void_p
        self._kernel32.ReadFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._kernel32.ReadFile.restype = ctypes.c_int
        self._kernel32.WriteFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._kernel32.WriteFile.restype = ctypes.c_int
        self._handle: int | None = None

    def connect(self, timeout: float = 5.0) -> None:
        """Open the pipe, waiting for the server to create it."""
        deadline = time.monotonic() + timeout
        while True:
            handle = self._kernel32.CreateFileW(
                pipe_path(self.name),
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if handle != INVALID_HANDLE_VALUE and handle is not None:
                self._handle = handle
                return
            if time.monotonic() > deadline:
                raise SimulationError("the client could not open the pipe")
            time.sleep(0.05)

    def send(self, payload: bytes) -> None:
        """Write one message."""
        written = ctypes.c_uint32(0)
        buffer = ctypes.create_string_buffer(payload, len(payload))
        self._kernel32.WriteFile(
            self._handle, buffer, len(payload), ctypes.byref(written), None
        )

    def receive(self, size: int = 4096) -> bytes:
        """Read one message."""
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_uint32(0)
        self._kernel32.ReadFile(
            self._handle, buffer, size, ctypes.byref(read), None
        )
        return buffer.raw[: read.value]

    def close(self) -> None:
        """Close the pipe."""
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def test_a_client_can_exchange_messages() -> None:
    name = "mt5_backtest_sim_test_exchange"
    server = PipeServer(name)
    received: list[bytes] = []

    def serve() -> None:
        server.listen()
        received.append(server.receive() or b"")
        server.send(b"pong")
        time.sleep(0.2)
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client = PipeClient(name)
    client.connect()
    client.send(b"ping")
    assert client.receive() == b"pong"
    client.close()
    thread.join(timeout=5)

    assert received == [b"ping"]


def test_the_server_reports_a_disconnected_client() -> None:
    name = "mt5_backtest_sim_test_disconnect"
    server = PipeServer(name)
    outcome: list[bytes | None] = []

    def serve() -> None:
        server.listen()
        outcome.append(server.receive())
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client = PipeClient(name)
    client.connect()
    client.close()
    thread.join(timeout=5)

    assert outcome == [None]


def test_the_wait_for_an_expert_is_bounded() -> None:
    server = PipeServer("mt5_backtest_sim_test_timeout")
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        server.listen(timeout_ms=1500)
    assert time.monotonic() - started < 10


def test_a_busy_pipe_is_reported() -> None:
    # The expert may still hold the pipe open when the simulator restarts.
    name = "mt5_backtest_sim_test_busy"
    first = PipeServer(name)

    def serve() -> None:
        first.listen()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client = PipeClient(name)
    client.connect()
    thread.join(timeout=5)

    try:
        second = PipeServer(name)
        with pytest.raises(SimulationError) as error:
            second.listen(timeout_ms=500)
        assert "pipe" in str(error.value).lower()
    finally:
        client.close()
        first.close()
