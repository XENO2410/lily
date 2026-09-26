"""ComputerState — Lily's live view of what's happening on the machine.

Populated by the agent after each tool run. Read by planners and context resolvers.
Thread-safe (agent may run in one thread, tray in another).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComputerState:
    active_app: str | None = None
    active_window_title: str | None = None
    last_command: str | None = None
    last_tool: str | None = None
    last_result_ok: bool | None = None
    last_command_at: float | None = None
    pending_confirmation: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if k in {"_lock"}:
                    continue
                if hasattr(self, k):
                    setattr(self, k, v)
                else:
                    self.extras[k] = v
            self.last_command_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_app": self.active_app,
                "active_window_title": self.active_window_title,
                "last_command": self.last_command,
                "last_tool": self.last_tool,
                "last_result_ok": self.last_result_ok,
                "last_command_at": self.last_command_at,
                "pending_confirmation": self.pending_confirmation,
                "extras": dict(self.extras),
            }
