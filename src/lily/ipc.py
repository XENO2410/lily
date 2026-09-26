"""Named-pipe IPC so `lily do "..."` can talk to the running daemon.

Protocol: newline-delimited JSON. One request per connection.

Requests:  {"cmd": "do",     "utterance": "..."}
           {"cmd": "status"}
           {"cmd": "stop"}

Responses: {"ok": bool, ...payload}

Windows-only (uses pywin32 named pipes). On non-Windows the server is a no-op.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from .logging import get_logger

log = get_logger("ipc")

_PIPE_PREFIX = r"\\.\pipe\\"


def pipe_path(name: str) -> str:
    return f"{_PIPE_PREFIX}{name}"


Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class NamedPipeServer:
    """Accepts one JSON message per connection, replies with one JSON line."""

    def __init__(self, name: str, handler: Handler,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.name = name
        self._handler = handler
        self._loop = loop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if sys.platform != "win32":
            log.warning("ipc: non-Windows, server disabled")
            return
        self._thread = threading.Thread(
            target=self._run, name="lily-ipc", daemon=True
        )
        self._thread.start()
        log.info("ipc: pipe listening on %s", pipe_path(self.name))

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:  # pragma: no cover - platform specific
        import pywintypes
        import win32file
        import win32pipe

        while not self._stop.is_set():
            try:
                handle = win32pipe.CreateNamedPipe(
                    pipe_path(self.name),
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    1,        # max instances (we serve one client at a time)
                    65536, 65536,
                    0,
                    None,
                )
            except Exception:
                log.exception("ipc: CreateNamedPipe failed")
                return

            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as e:
                # ERROR_PIPE_CONNECTED (535) = client connected before ConnectNamedPipe
                if e.winerror != 535:
                    log.warning("ipc: ConnectNamedPipe: %s", e)
                    win32file.CloseHandle(handle)
                    continue

            try:
                _, raw = win32file.ReadFile(handle, 65536)
                request = json.loads(raw.decode("utf-8"))
                # Dispatch on the main asyncio loop and wait for the result.
                future = asyncio.run_coroutine_threadsafe(
                    self._handler(request), self._loop
                )
                response = future.result(timeout=30)
            except Exception as e:
                log.exception("ipc: request failed")
                response = {"ok": False, "error": f"{type(e).__name__}: {e}"}

            try:
                win32file.WriteFile(handle, json.dumps(response).encode("utf-8"))
                win32file.FlushFileBuffers(handle)
            except Exception:
                log.exception("ipc: write reply failed")
            finally:
                with contextlib.suppress(Exception):
                    win32pipe.DisconnectNamedPipe(handle)
                win32file.CloseHandle(handle)


def send_request(name: str, request: dict[str, Any], timeout_s: float = 5.0
                 ) -> dict[str, Any] | None:
    """Client: send one JSON message, get one reply. None if no server."""
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - platform specific
        import pywintypes
        import win32file
    except Exception:
        return None

    try:
        handle = win32file.CreateFile(
            pipe_path(name),
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None, win32file.OPEN_EXISTING, 0, None,
        )
    except pywintypes.error as e:
        # ERROR_FILE_NOT_FOUND (2) or ERROR_PIPE_BUSY (231): no live daemon.
        if e.winerror in (2, 231):
            return None
        log.warning("ipc client connect failed: %s", e)
        return None

    try:
        win32file.WriteFile(handle, json.dumps(request).encode("utf-8"))
        _, raw = win32file.ReadFile(handle, 65536)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        log.exception("ipc client io failed")
        return None
    finally:
        with contextlib.suppress(Exception):
            win32file.CloseHandle(handle)


def is_running(name: str) -> bool:
    return send_request(name, {"cmd": "status"}, timeout_s=1.5) is not None
