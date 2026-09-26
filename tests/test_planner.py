"""LLM planner tests — mocked OpenAI async client.

Proves:
    - direct reply flow (no tool call)
    - tool-call flow (one tool, round-2 reply)
    - tool-call flow (unknown tool → failure, not crash)
    - schema round-trip (apps.launch <-> apps__launch)
    - is_available reflects config correctly
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from lily.config import LilySettings
from lily.core.planner import LLMPlanner
from lily.tools.base import NoArgs, PermissionLevel, Tool, ToolResult
from lily.tools.registry import ToolRegistry


class OpenArgs(BaseModel):
    app: str


def _stub_open(a: OpenArgs) -> ToolResult:
    return ToolResult.success(launched=a.app, via="stub")


def _stub_time(_: NoArgs) -> ToolResult:
    return ToolResult.success(time="9:57 PM", hour=21, minute=57)


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(Tool("apps.launch", "Open an app.", OpenArgs, _stub_open))
    r.register(Tool("system.time", "Current local time.", NoArgs, _stub_time))
    return r


@pytest.fixture
def enabled_settings() -> LilySettings:
    # LilySettings picks up env vars — override in-instance for tests.
    s = LilySettings()
    s.llm_provider = "openrouter"
    s.llm_model = "openai/gpt-4o-mini"
    s.llm_api_key = "sk-test"
    s.llm_base_url = "https://openrouter.example/api/v1"
    return s


def _msg(content=None, tool_calls=None):
    """Build a fake OpenAI response message."""
    if tool_calls:
        calls = [
            SimpleNamespace(
                id=f"call_{i}",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for i, (name, args) in enumerate(tool_calls)
        ]
    else:
        calls = None
    m = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=m)])


def _install_mock_client(planner: LLMPlanner, *responses):
    """Inject an AsyncMock client that returns the given responses in order."""
    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace(create=AsyncMock(side_effect=responses))
    planner._client = client  # skip real openai import
    return client


# ── availability ────────────────────────────────────────────────
def test_is_available_off_by_default(registry: ToolRegistry) -> None:
    s = LilySettings()
    s.llm_provider = "none"
    s.llm_api_key = ""
    p = LLMPlanner(s, registry)
    assert p.is_available() is False


def test_is_available_requires_key(registry: ToolRegistry) -> None:
    s = LilySettings()
    s.llm_provider = "openrouter"
    s.llm_model = "openai/gpt-4o-mini"
    s.llm_api_key = ""
    p = LLMPlanner(s, registry)
    assert p.is_available() is False


def test_is_available_on(registry: ToolRegistry, enabled_settings: LilySettings) -> None:
    p = LLMPlanner(enabled_settings, registry)
    assert p.is_available() is True


# ── schema conversion ─────────────────────────────────────────
def test_tool_schemas_convert_dots(registry: ToolRegistry, enabled_settings) -> None:
    p = LLMPlanner(enabled_settings, registry)
    schemas = p._tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "apps__launch" in names
    assert "system__time" in names
    # Round trip
    assert p._from_oa_name("apps__launch") == "apps.launch"


# ── plan_and_execute paths ───────────────────────────────────
@pytest.mark.asyncio
async def test_direct_reply(registry: ToolRegistry, enabled_settings) -> None:
    p = LLMPlanner(enabled_settings, registry)
    _install_mock_client(p, _msg(content="Hello! I'm here to help."))
    result = await p.plan_and_execute("hi lily")
    assert result.kind == "reply"
    assert "Hello" in result.reply
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_tool_call_executes_and_replies(
    registry: ToolRegistry, enabled_settings
) -> None:
    p = LLMPlanner(enabled_settings, registry)
    _install_mock_client(
        p,
        _msg(tool_calls=[("system__time", {})]),           # round 1
        _msg(content="It's 9:57 PM."),                     # round 2
    )
    result = await p.plan_and_execute("what time is it")
    assert result.kind == "tools"
    assert result.tool_calls == [("system.time", {})]
    assert len(result.tool_results) == 1
    assert result.tool_results[0].ok is True
    assert result.tool_results[0].data.get("time") == "9:57 PM"
    assert result.reply == "It's 9:57 PM."


@pytest.mark.asyncio
async def test_tool_call_with_args(
    registry: ToolRegistry, enabled_settings
) -> None:
    p = LLMPlanner(enabled_settings, registry)
    _install_mock_client(
        p,
        _msg(tool_calls=[("apps__launch", {"app": "notepad"})]),
        _msg(content="Opening Notepad."),
    )
    result = await p.plan_and_execute("please open notepad")
    assert result.kind == "tools"
    assert result.tool_calls == [("apps.launch", {"app": "notepad"})]
    assert result.tool_results[0].data.get("launched") == "notepad"


@pytest.mark.asyncio
async def test_unknown_tool_fails_softly(
    registry: ToolRegistry, enabled_settings
) -> None:
    p = LLMPlanner(enabled_settings, registry)
    _install_mock_client(
        p,
        _msg(tool_calls=[("weather__forecast", {"city": "Tokyo"})]),
        _msg(content="Sorry, I can't check the weather yet."),
    )
    result = await p.plan_and_execute("what's the weather in tokyo")
    assert result.kind == "tools"
    assert result.tool_results[0].ok is False
    assert "unknown tool" in (result.tool_results[0].error or "")


@pytest.mark.asyncio
async def test_permission_denial(
    registry: ToolRegistry, enabled_settings
) -> None:
    async def deny(_tool):
        return False

    registry.register(
        Tool("dangerous.action", "Danger.", NoArgs,
             lambda _: ToolResult.success(),
             permission=PermissionLevel.DESTRUCTIVE)
    )
    p = LLMPlanner(enabled_settings, registry, permit_fn=deny)
    _install_mock_client(
        p,
        _msg(tool_calls=[("dangerous__action", {})]),
        _msg(content="Cancelled."),
    )
    result = await p.plan_and_execute("do the dangerous thing")
    assert result.tool_results[0].ok is False
    assert "permission" in (result.tool_results[0].error or "").lower()
