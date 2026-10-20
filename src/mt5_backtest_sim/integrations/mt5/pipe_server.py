"""A Windows named pipe server.

The simulator owns the pipe and the expert advisor connects to it. Windows named
pipes have no binding in the Python standard library, so the server talks to
``kernel32`` directly through :mod:`ctypes`: no third party dependency, no compiled
extension, and the exact semantics the protocol needs (message mode, one message per
read).

The class is Windows only. On other systems it raises immediately with a clear
message rather than failing later in a confusing way.
"""

from __future__ import annotations

import sys
from typing import Final

from mt5_backtest_sim.domain.exceptions import SimulationError

__all__ = ["PipeServer", "pipe_path"]

PIPE_ACCESS_DUPLEX: Final = 0x00000003
PIPE_TYPE_MESSAGE: Final = 0x00000004
PIPE_READMODE_MESSAGE: Final = 0x00000002
PIPE_WAIT: Final = 0x00000000
ERROR_BROKEN_PIPE: Final = 109
ERROR_PIPE_CONNECTED: Final = 535
INVALID_HANDLE_VALUE: Final = -1
DEFAULT_BUFFER_SIZE: Final = 65536


def pipe_path(pipe_name: str) -> str:
    """The full path of a named pipe."""
    return rf"\\.\pipe\{pipe_name}"


class PipeServer:
    """A named pipe server that speaks one message per read and write."""

    def __init__(self, pipe_name: str, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        if sys.platform != "win32":
            raise SimulationError(
                "the MetaTrader 5 bridge needs Windows named pipes; "
                f"this host reports {sys.platform}"
            )
        self.pipe_name = pipe_name
        self.path = pipe_path(pipe_name)
        self._buffer_size = buffer_size
        self._handle: int | None = None
        self._kernel32 = self._load_kernel32()

    @staticmethod
    def _load_kernel32() -> object:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateNamedPipeW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateNamedPipeW.restype = ctypes.c_void_p
        kernel32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.ConnectNamedPipe.restype = ctypes.c_int
        kernel32.ReadFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = ctypes.c_int
        kernel32.WriteFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = ctypes.c_int
        kernel32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        return kernel32

    # ------------------------------------------------------------------ server

    def listen(self) -> None:
        """Create the pipe and wait for the expert to connect."""
        import ctypes

        handle = self._kernel32.CreateNamedPipeW(  # type: ignore[attr-defined]
            self.path,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1,
            self._buffer_size,
            self._buffer_size,
            0,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            error = ctypes.get_last_error()
            raise SimulationError(
                f"cannot create the named pipe {self.path} (Windows error {error})"
            )
        self._handle = handle
        connected = self._kernel32.ConnectNamedPipe(handle, None)  # type: ignore[attr-defined]
        if not connected:
            error = ctypes.get_last_error()
            if error != ERROR_PIPE_CONNECTED:
                raise SimulationError(
                    f"the named pipe {self.path} was not connected (Windows error {error})"
                )

    # ------------------------------------------------------------------- i/o

    def send(self, payload: bytes) -> None:
        """Write one message to the connected client."""
        import ctypes

        if self._handle is None:
            raise SimulationError("the pipe is not connected")
        written = ctypes.c_uint32(0)
        buffer = ctypes.create_string_buffer(payload, len(payload))
        ok = self._kernel32.WriteFile(  # type: ignore[attr-defined]
            self._handle,
            buffer,
            len(payload),
            ctypes.byref(written),
            None,
        )
        if not ok:
            error = ctypes.get_last_error()
            raise SimulationError(
                f"cannot write to the named pipe (Windows error {error})"
            )

    def receive(self) -> bytes | None:
        """Read one message, or ``None`` when the client disconnected."""
        import ctypes

        if self._handle is None:
            raise SimulationError("the pipe is not connected")
        buffer = ctypes.create_string_buffer(self._buffer_size)
        read = ctypes.c_uint32(0)
        ok = self._kernel32.ReadFile(  # type: ignore[attr-defined]
            self._handle,
            buffer,
            self._buffer_size,
            ctypes.byref(read),
            None,
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == ERROR_BROKEN_PIPE:
                return None
            raise SimulationError(
                f"cannot read from the named pipe (Windows error {error})"
            )
        if read.value == 0:
            return None
        return buffer.raw[: read.value]

    def close(self) -> None:
        """Disconnect and close the pipe."""
        if self._handle is None:
            return
        self._kernel32.DisconnectNamedPipe(self._handle)  # type: ignore[attr-defined]
        self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        self._handle = None

    def __enter__(self) -> PipeServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
