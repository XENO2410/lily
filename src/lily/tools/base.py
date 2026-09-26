"""Tool contract: what every Lily capability looks like from the outside."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel


class PermissionLevel(str, Enum):
    """How risky is executing this tool?

    SAFE:        run immediately (volume, launch app, screenshot).
    SENSITIVE:   confirm first (send message, spend money).
    DESTRUCTIVE: confirm first, log loudly (delete file, shutdown, run shell).
    """

    SAFE = "safe"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


class NoArgs(BaseModel):
    """Placeholder args model for tools that take nothing."""


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0

    @classmethod
    def success(cls, **data: Any) -> ToolResult:
        return cls(ok=True, data=dict(data))

    @classmethod
    def failure(cls, error: str, **data: Any) -> ToolResult:
        return cls(ok=False, error=error, data=dict(data))


# Handlers receive a validated pydantic model instance and return ToolResult.
# Async handlers are supported for I/O-heavy tools (browser, network).
Handler = Callable[[BaseModel], ToolResult] | Callable[[BaseModel], Awaitable[ToolResult]]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    permission: PermissionLevel = PermissionLevel.SAFE
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise ValueError(
                f"tool name must be dotted like 'category.action', got: {self.name!r}"
            )
