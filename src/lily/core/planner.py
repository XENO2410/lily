"""LLM planner — OpenAI-compatible (OpenRouter) function calling.

Sits behind the fast router. When the router misses, the agent asks the planner:
  - "Can you handle this?"
  - Planner sends the utterance + registered tool schemas + system prompt
  - LLM either replies directly OR emits typed tool_calls
  - We validate & execute the tool(s) through the same ToolRegistry
  - We feed the results back to the LLM for a natural spoken response

Safety:
  - LLM never sees implementations, only descriptions + arg schemas.
  - Every tool call goes through the normal permission gate (via injected callback).
  - Args are Pydantic-validated by ToolRegistry.invoke before anything runs.
  - Tool names round-trip through '.' <-> '__' because OpenAI disallows dots in
    function names but our namespace is dotted.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import LilySettings
from ..logging import get_logger
from ..tools.base import Tool, ToolResult
from ..tools.registry import ToolRegistry

log = get_logger("planner")

# Permission callback: given a Tool, returns True if it may run. Async or sync.
PermitFn = Callable[[Tool], bool] | Callable[[Tool], Awaitable[bool]]


@dataclass
class PlannerResult:
    kind: str = "unavailable"   # 'reply' | 'tools' | 'error' | 'unavailable'
    reply: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    error: str | None = None


class LLMPlanner:
    def __init__(
        self,
        settings: LilySettings,
        registry: ToolRegistry,
        permit_fn: PermitFn | None = None,
    ) -> None:
        self._s = settings
        self._registry = registry
        self._permit_fn = permit_fn
        self._client = None
        self._schemas_cache: list[dict[str, Any]] | None = None

    # ── availability ────────────────────────────────────────────
    def is_available(self) -> bool:
        return bool(
            self._s.llm_provider not in ("none", "")
            and self._s.llm_model
            and self._s.llm_api_key
        )

    # ── openai client (lazy) ────────────────────────────────────
    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            log.error("openai package unavailable: %s", e)
            return None
        self._client = AsyncOpenAI(
            base_url=self._s.llm_base_url or None,
            api_key=self._s.llm_api_key,
        )
        return self._client

    # ── tool name <-> openai function name ──────────────────────
    @staticmethod
    def _oa_name(tool_name: str) -> str:
        return tool_name.replace(".", "__")

    @staticmethod
    def _from_oa_name(name: str) -> str:
        return name.replace("__", ".")

    # ── tool schemas (cached; registry is stable per run) ───────
    def _tool_schemas(self) -> list[dict[str, Any]]:
        if self._schemas_cache is not None:
            return self._schemas_cache
        out: list[dict[str, Any]] = []
        for tool in self._registry.list_all():
            try:
                params = tool.args_model.model_json_schema()
                params.pop("title", None)
                for prop in params.get("properties", {}).values():
                    prop.pop("title", None)
                out.append({
                    "type": "function",
                    "function": {
                        "name": self._oa_name(tool.name),
                        "description": tool.description,
                        "parameters": params,
                    },
                })
            except Exception:
                log.exception("could not build schema for %s", tool.name)
        self._schemas_cache = out
        log.info("planner: %d tools exposed to LLM", len(out))
        return out

    # ── system prompt ───────────────────────────────────────────
    def _system_prompt(self) -> str:
        capabilities = self._capabilities_summary()
        return (
            f"You are {self._s.name}, a helpful personal assistant on the user's Windows PC. "
            "Be brief and warm — respond in one short sentence. "
            "If the user asks you to do something on the computer, USE a tool from "
            "the list below; do not describe the action, actually call the tool. "
            "For questions that don't require an action (facts, translations, "
            "quick math, chit-chat), answer directly. "
            "If asked what you can do, describe your capabilities honestly and "
            "concisely based on the tools listed. Do not invent capabilities. "
            "If asked to do something outside your tools, admit it plainly and "
            "suggest an alternative if possible. "
            "For destructive actions, expect the app to prompt the user for confirmation.\n\n"
            f"Available capabilities:\n{capabilities}"
        )

    def _capabilities_summary(self) -> str:
        """Group tools by namespace so the LLM can describe them accurately."""
        by_ns: dict[str, list[str]] = {}
        for tool in self._registry.list_all():
            ns, _, _ = tool.name.partition(".")
            by_ns.setdefault(ns, []).append(
                f"- {tool.name}: {tool.description}"
            )
        parts: list[str] = []
        for ns in sorted(by_ns):
            parts.append(f"{ns}:")
            parts.extend(by_ns[ns])
        return "\n".join(parts)

    # ── main entry ──────────────────────────────────────────────
    async def plan_and_execute(self, utterance: str) -> PlannerResult:
        if not self.is_available():
            return PlannerResult(kind="unavailable")

        client = self._get_client()
        if client is None:
            return PlannerResult(kind="error", error="openai client unavailable")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": utterance},
        ]

        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self._s.llm_model,
                    messages=messages,
                    tools=self._tool_schemas(),
                    tool_choice="auto",
                    temperature=0.2,
                ),
                timeout=self._s.llm_timeout_s,
            )
        except asyncio.TimeoutError:
            return PlannerResult(kind="error",
                                 error=f"LLM timeout ({self._s.llm_timeout_s}s)")
        except Exception as e:
            log.exception("llm call failed")
            return PlannerResult(kind="error", error=f"{type(e).__name__}: {e}")

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        # No tools requested — direct answer.
        if not tool_calls:
            reply = (msg.content or "").strip()
            log.info("LLM reply (no tools): %r", reply[:200])
            return PlannerResult(kind="reply", reply=reply)

        # Execute each tool call through the registry (typed + permission-gated).
        log.info("LLM chose tools: %s",
                 [self._from_oa_name(c.function.name) for c in tool_calls])
        executed: list[tuple[str, dict[str, Any]]] = []
        results: list[ToolResult] = []
        tool_result_msgs: list[dict[str, Any]] = []

        for call in tool_calls:
            oa_name = call.function.name
            tool_name = self._from_oa_name(oa_name)
            try:
                args = json.loads(call.function.arguments or "{}")
            except Exception:
                args = {}
            executed.append((tool_name, args))

            tool = self._registry.get(tool_name)
            if tool is None:
                result = ToolResult.failure(f"unknown tool: {tool_name}")
            elif not await self._permitted(tool):
                result = ToolResult.failure("permission denied by user")
            else:
                result = await self._registry.invoke(tool_name, args)

            log.info("  %s(%s) -> ok=%s", tool_name, args, result.ok)
            results.append(result)
            tool_result_msgs.append({
                "tool_call_id": call.id,
                "role": "tool",
                "name": oa_name,
                "content": json.dumps({
                    "ok": result.ok,
                    "data": result.data,
                    "error": result.error,
                }),
            })

        # Round 2: hand results back so the LLM can craft a natural reply.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.function.name,
                        "arguments": c.function.arguments,
                    },
                }
                for c in tool_calls
            ],
        })
        messages.extend(tool_result_msgs)

        reply = ""
        try:
            resp2 = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self._s.llm_model,
                    messages=messages,
                    temperature=0.2,
                ),
                timeout=self._s.llm_timeout_s,
            )
            reply = (resp2.choices[0].message.content or "").strip()
        except Exception:
            log.exception("llm round-2 failed; falling back to canned reply")
            reply = "done" if all(r.ok for r in results) else "that didn't fully work"

        log.info("LLM final reply: %r", reply[:200])
        return PlannerResult(
            kind="tools",
            reply=reply,
            tool_calls=executed,
            tool_results=results,
        )

    async def _permitted(self, tool: Tool) -> bool:
        if self._permit_fn is None:
            return True
        try:
            result = self._permit_fn(tool)
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except Exception:
            log.exception("permit_fn crashed — denying")
            return False
