"""A Windows named pipe server.

The simulator owns the pipe and the expert advisor connects to it. Windows named
pipes have no binding in the Python standard library, so the server talks to
``kernel32`` directly through :mod:`ctypes`: no third party dependency, no compiled
extension, and the exact semantics the protocol needs (message mode, one message per
read).

The handle is opened in *overlapped* mode. That is the only way to bound a wait: the
server has to be able to stop waiting for the expert after a while, and a
synchronous ``ConnectNamedPipe`` can only be cancelled by the thread that issued it.

The class is Windows only. On other systems it raises immediately with a clear
message rather than failing later in a confusing way.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Final

from mt5_backtest_sim.domain.exceptions import SimulationError

__all__ = ["PipeServer", "pipe_path"]

PIPE_ACCESS_DUPLEX: Final = 0x00000003
PIPE_TYPE_MESSAGE: Final = 0x00000004
PIPE_READMODE_MESSAGE: Final = 0x00000002
PIPE_WAIT: Final = 0x00000000
FILE_FLAG_OVERLAPPED: Final = 0x40000000
INVALID_HANDLE_VALUE: Final = -1
DEFAULT_BUFFER_SIZE: Final = 65536

ERROR_BROKEN_PIPE: Final = 109
ERROR_PIPE_CONNECTED: Final = 535
ERROR_NO_DATA: Final = 232
ERROR_OPERATION_ABORTED: Final = 995
ERROR_IO_PENDING: Final = 997
WAIT_OBJECT_0: Final = 0
WAIT_TIMEOUT: Final = 258
INFINITE: Final = 0xFFFFFFFF


class OVERLAPPED(ctypes.Structure):
    """The overlapped structure Windows uses for asynchronous operations.

    ``Internal`` and ``InternalHigh`` are pointer sized, so the structure is laid
    out differently on a 64 bit host than on a 32 bit one.
    """

    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", ctypes.c_ulong),
        ("OffsetHigh", ctypes.c_ulong),
        ("hEvent", wintypes.HANDLE),
    ]


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
        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
        kernel32.ConnectNamedPipe.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(OVERLAPPED),
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(OVERLAPPED),
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(OVERLAPPED),
            ctypes.POINTER(ctypes.c_uint32),
            wintypes.BOOL,
        ]
        kernel32.GetOverlappedResult.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.CancelIoEx.restype = wintypes.BOOL
        kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        return kernel32

    # ------------------------------------------------------------------ server

    def listen(self, timeout_ms: int | None = None) -> None:
        """Create the pipe and wait for the expert to connect.

        ``timeout_ms`` bounds the wait. Without it the call waits for as long as the
        expert takes, which is what a server that is started before the terminal wants.
        """
        handle = self._kernel32.CreateNamedPipeW(  # type: ignore[attr-defined]
            self.path,
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1,
            self._buffer_size,
            self._buffer_size,
            0,
            None,
        )
        # A Windows handle that is invalid is 0xFFFFFFFFFFFFFFFF. The value arrives
        # as an unsigned integer, so comparing it with -1 would never match.
        if not handle or handle in (INVALID_HANDLE_VALUE, 0xFFFFFFFFFFFFFFFF):
            error = ctypes.get_last_error()
            raise SimulationError(
                f"cannot create the named pipe {self.path} (Windows error {error}). "
                "Another client may still hold the pipe open."
            )
        self._handle = handle

        overlapped = OVERLAPPED()
        ctypes.set_last_error(0)
        connected = self._kernel32.ConnectNamedPipe(  # type: ignore[attr-defined]
            handle, ctypes.byref(overlapped)
        )
        if not connected and ctypes.get_last_error() != ERROR_IO_PENDING:
            error = ctypes.get_last_error()
            if error == ERROR_PIPE_CONNECTED:
                # A client attached between the creation of the pipe and this call.
                return
            if error == ERROR_NO_DATA:
                # The client attached and left again before this call completed. The
                # pipe is open and the next read will report the disconnection.
                return
            self.close()
            raise SimulationError(
                f"the named pipe {self.path} was not connected (Windows error {error})"
            )

        wait = INFINITE if timeout_ms is None else timeout_ms
        result = self._kernel32.WaitForSingleObject(handle, wait)  # type: ignore[attr-defined]
        if result == WAIT_OBJECT_0:
            return
        if result == WAIT_TIMEOUT:
            self._kernel32.CancelIoEx(handle, None)  # type: ignore[attr-defined]
            self.close()
            raise TimeoutError(
                f"no expert connected to {self.path} within {timeout_ms} ms"
            )
        error = ctypes.get_last_error()
        self.close()
        raise SimulationError(
            f"waiting for the expert on {self.path} failed (Windows error {error})"
        )

    # ------------------------------------------------------------------- i/o

    def _wait(self, timeout_ms: int | None) -> bool:
        """Wait for an overlapped operation to finish."""
        wait = INFINITE if timeout_ms is None else timeout_ms
        result = int(self._kernel32.WaitForSingleObject(  # type: ignore[attr-defined]
            self._handle, wait
        ))
        return result == WAIT_OBJECT_0

    def send(self, payload: bytes, timeout_ms: int | None = None) -> None:
        """Write one message to the connected client."""
        if self._handle is None:
            raise SimulationError("the pipe is not connected")
        buffer = ctypes.create_string_buffer(payload, len(payload))
        written = ctypes.c_uint32(0)
        overlapped = OVERLAPPED()
        ok = self._kernel32.WriteFile(  # type: ignore[attr-defined]
            self._handle,
            buffer,
            len(payload),
            ctypes.byref(written),
            ctypes.byref(overlapped),
        )
        if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
            error = ctypes.get_last_error()
            raise SimulationError(f"cannot write to the named pipe (Windows error {error})")
        if not self._wait(timeout_ms):
            self._kernel32.CancelIoEx(self._handle, None)  # type: ignore[attr-defined]
            raise TimeoutError("the terminal did not accept the message in time")

    def receive(self, timeout_ms: int | None = None) -> bytes | None:
        """Read one message, or ``None`` when the client disconnected."""
        if self._handle is None:
            raise SimulationError("the pipe is not connected")
        buffer = ctypes.create_string_buffer(self._buffer_size)
        read = ctypes.c_uint32(0)
        overlapped = OVERLAPPED()
        ok = self._kernel32.ReadFile(  # type: ignore[attr-defined]
            self._handle,
            buffer,
            self._buffer_size,
            ctypes.byref(read),
            ctypes.byref(overlapped),
        )
        if not ok:
            error = ctypes.get_last_error()
            if error in (ERROR_BROKEN_PIPE, ERROR_OPERATION_ABORTED, ERROR_NO_DATA):
                return None
            if error != ERROR_IO_PENDING:
                raise SimulationError(
                    f"cannot read from the named pipe (Windows error {error})"
                )
        if not self._wait(timeout_ms):
            self._kernel32.CancelIoEx(self._handle, None)  # type: ignore[attr-defined]
            raise TimeoutError("the terminal did not answer in time")
        transferred = ctypes.c_uint32(0)
        self._kernel32.GetOverlappedResult(  # type: ignore[attr-defined]
            self._handle, ctypes.byref(overlapped), ctypes.byref(transferred), True
        )
        if transferred.value == 0:
            return None
        return buffer.raw[: transferred.value]

    def close(self) -> None:
        """Disconnect and close the pipe."""
        if self._handle is None:
            return
        self._kernel32.DisconnectNamedPipe(self._handle)  # type: ignore[attr-defined]
        self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        self._handle = None

    @property
    def is_connected(self) -> bool:
        """Whether the pipe is open."""
        return self._handle is not None

    def __enter__(self) -> PipeServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
