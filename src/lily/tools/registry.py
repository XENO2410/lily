"""Central tool registry. Validates args, times execution, wraps errors."""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from pydantic import ValidationError

from ..logging import get_logger
from .base import Tool, ToolResult

log = get_logger("tools")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        log.debug("registered tool: %s (%s)", tool.name, tool.permission.value)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def invoke(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Validate args, run the handler, return a ToolResult with timing.

        Never raises — errors are captured into ToolResult.failure.
        Permission checks happen in the Agent (which owns the confirm_fn); the
        registry is the last-mile executor.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"unknown tool: {name}")

        try:
            model = tool.args_model(**(args or {}))
        except ValidationError as e:
            return ToolResult.failure(f"invalid args: {e.errors()}")

        started = time.perf_counter()
        try:
            result = tool.handler(model)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=tool.timeout_s)
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - started) * 1000
            log.warning("tool timeout: %s after %.0fms", name, duration_ms)
            return ToolResult(ok=False, error=f"timeout after {tool.timeout_s}s",
                              duration_ms=duration_ms)
        except Exception as e:
            duration_ms = (time.perf_counter() - started) * 1000
            log.exception("tool crashed: %s", name)
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}",
                              duration_ms=duration_ms)

        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                f"tool {name} returned {type(result).__name__}, expected ToolResult"
            )
        result.duration_ms = (time.perf_counter() - started) * 1000
        return result
