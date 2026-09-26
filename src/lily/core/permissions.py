"""Permission gating.

The Agent asks the PermissionManager whether a tool needs a confirm-step.
Confirmation is delegated back to whatever surface is asking (console/tray/voice)
via a pluggable confirm_fn.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..logging import get_logger
from ..tools.base import PermissionLevel

log = get_logger("permissions")

ConfirmFn = Callable[[str], bool] | Callable[[str], Awaitable[bool]]


class PermissionManager:
    def __init__(self, confirm_destructive: bool = True,
                 confirm_fn: ConfirmFn | None = None) -> None:
        self.confirm_destructive = confirm_destructive
        self._confirm_fn: ConfirmFn | None = confirm_fn

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self._confirm_fn = fn

    def requires_confirmation(self, level: PermissionLevel) -> bool:
        if level == PermissionLevel.SAFE:
            return False
        if level == PermissionLevel.DESTRUCTIVE:
            return self.confirm_destructive
        # SENSITIVE
        return self.confirm_destructive

    async def confirm(self, prompt: str) -> bool:
        if self._confirm_fn is None:
            log.warning("no confirm_fn registered — denying by default: %s", prompt)
            return False
        import inspect
        result = self._confirm_fn(prompt)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
