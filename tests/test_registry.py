"""Tool registry: register, validate, invoke, error handling."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from lily.tools.base import NoArgs, PermissionLevel, Tool, ToolResult
from lily.tools.registry import ToolRegistry


class Args(BaseModel):
    x: int = Field(ge=0, le=100)


def _handler(a: Args) -> ToolResult:
    return ToolResult.success(doubled=a.x * 2)


def _fail_handler(_: NoArgs) -> ToolResult:
    return ToolResult.failure("broken")


def _crash_handler(_: NoArgs) -> ToolResult:
    raise RuntimeError("boom")


async def _slow_handler(_: NoArgs) -> ToolResult:
    await asyncio.sleep(0.5)
    return ToolResult.success()


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(Tool("math.double", "Double a number.", Args, _handler))
    r.register(Tool("misc.fail", "Always fails.", NoArgs, _fail_handler))
    r.register(Tool("misc.crash", "Raises.", NoArgs, _crash_handler))
    r.register(Tool("misc.slow", "Sleeps.", NoArgs, _slow_handler, timeout_s=0.1))
    return r


def test_duplicate_registration_rejected(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        registry.register(Tool("math.double", "dup", Args, _handler))


def test_bad_tool_name_rejected() -> None:
    with pytest.raises(ValueError):
        Tool("noNamespace", "bad", NoArgs, _fail_handler)


@pytest.mark.asyncio
async def test_invoke_happy(registry: ToolRegistry) -> None:
    r = await registry.invoke("math.double", {"x": 7})
    assert r.ok is True
    assert r.data == {"doubled": 14}
    assert r.duration_ms >= 0


@pytest.mark.asyncio
async def test_invoke_validates(registry: ToolRegistry) -> None:
    r = await registry.invoke("math.double", {"x": 999})
    assert r.ok is False
    assert "invalid args" in (r.error or "")


@pytest.mark.asyncio
async def test_invoke_unknown_tool(registry: ToolRegistry) -> None:
    r = await registry.invoke("nope.nada", {})
    assert r.ok is False
    assert "unknown tool" in (r.error or "")


@pytest.mark.asyncio
async def test_handler_failure_wrapped(registry: ToolRegistry) -> None:
    r = await registry.invoke("misc.fail", {})
    assert r.ok is False
    assert r.error == "broken"


@pytest.mark.asyncio
async def test_handler_crash_wrapped(registry: ToolRegistry) -> None:
    r = await registry.invoke("misc.crash", {})
    assert r.ok is False
    assert "RuntimeError" in (r.error or "")


@pytest.mark.asyncio
async def test_timeout(registry: ToolRegistry) -> None:
    r = await registry.invoke("misc.slow", {})
    assert r.ok is False
    assert "timeout" in (r.error or "").lower()


def test_permission_levels_available() -> None:
    assert PermissionLevel.SAFE.value == "safe"
    assert PermissionLevel.SENSITIVE.value == "sensitive"
    assert PermissionLevel.DESTRUCTIVE.value == "destructive"
