"""Permission gate behavior."""
from __future__ import annotations

import pytest

from lily.core.permissions import PermissionManager
from lily.tools.base import PermissionLevel


def test_safe_never_requires_confirmation() -> None:
    pm = PermissionManager(confirm_destructive=True)
    assert pm.requires_confirmation(PermissionLevel.SAFE) is False


def test_destructive_requires_when_flag_on() -> None:
    pm = PermissionManager(confirm_destructive=True)
    assert pm.requires_confirmation(PermissionLevel.DESTRUCTIVE) is True
    assert pm.requires_confirmation(PermissionLevel.SENSITIVE) is True


def test_flag_off_bypasses_confirmation() -> None:
    pm = PermissionManager(confirm_destructive=False)
    assert pm.requires_confirmation(PermissionLevel.DESTRUCTIVE) is False
    assert pm.requires_confirmation(PermissionLevel.SENSITIVE) is False


@pytest.mark.asyncio
async def test_confirm_delegates_to_registered_fn() -> None:
    pm = PermissionManager()
    calls: list[str] = []

    def _confirm(prompt: str) -> bool:
        calls.append(prompt)
        return True

    pm.set_confirm_fn(_confirm)
    assert await pm.confirm("delete file?") is True
    assert calls == ["delete file?"]


@pytest.mark.asyncio
async def test_no_confirm_fn_denies() -> None:
    pm = PermissionManager()
    assert await pm.confirm("send message?") is False
