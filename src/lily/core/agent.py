"""Agent — the orchestrator that turns an utterance into a validated tool run.

Path:
    utterance → router hits? → (permission → tool) → speak + log
                         └─miss→ LLM planner (if configured) → tool(s) / reply / error
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..config import LilySettings
from ..core.memory import Memory
from ..core.permissions import PermissionManager
from ..core.planner import LLMPlanner
from ..core.router import Router
from ..core.state import ComputerState
from ..logging import get_logger
from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry

log = get_logger("agent")


@dataclass
class AgentResult:
    ok: bool
    tool: str | None
    args: dict[str, Any]
    result: ToolResult
    duration_ms: float
    reason: str | None = None  # populated when we couldn't route

    def summary(self) -> str:
        if self.tool is None:
            return self.reason or "no match"
        if self.result.ok:
            return f"{self.tool} ok"
        return f"{self.tool} failed: {self.result.error}"


class Agent:
    def __init__(
        self,
        settings: LilySettings,
        registry: ToolRegistry,
        router: Router,
        memory: Memory,
        state: ComputerState,
        permissions: PermissionManager,
        speaker=None,
        planner: LLMPlanner | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.router = router
        self.memory = memory
        self.state = state
        self.permissions = permissions
        self.speaker = speaker
        self.planner = planner

    async def handle(self, utterance: str, *, source: str = "console") -> AgentResult:
        started = time.perf_counter()
        utterance = (utterance or "").strip()
        if not utterance:
            return AgentResult(False, None, {},
                               ToolResult.failure("empty input"),
                               0.0, reason="empty input")

        self.state.update(last_command=utterance)

        intent = self.router.match(utterance)
        if intent is None:
            return await self._handle_router_miss(utterance, source, started)

        tool = self.registry.get(intent.tool)
        if tool is None:
            reason = f"router pointed at missing tool: {intent.tool}"
            log.error(reason)
            return AgentResult(False, intent.tool, intent.args,
                               ToolResult.failure(reason),
                               (time.perf_counter() - started) * 1000,
                               reason=reason)

        if self.permissions.requires_confirmation(tool.permission):
            prompt = f"{tool.name} ({tool.permission.value}) — confirm?"
            self.state.update(pending_confirmation=prompt)
            confirmed = await self.permissions.confirm(prompt)
            self.state.update(pending_confirmation=None)
            if not confirmed:
                res = ToolResult.failure("cancelled by user")
                self._log(source, utterance, intent.tool, intent.args, False,
                          res.error, 0.0, None)
                return AgentResult(False, intent.tool, intent.args, res,
                                   (time.perf_counter() - started) * 1000,
                                   reason="cancelled")

        result = await self.registry.invoke(intent.tool, intent.args)
        duration_ms = (time.perf_counter() - started) * 1000

        # Surface the outcome at INFO so users can see it in the terminal.
        if result.ok:
            log.info("router hit: %r -> %s ok in %.0fms",
                     utterance, intent.tool, result.duration_ms)
        else:
            log.info("router hit: %r -> %s FAILED: %s",
                     utterance, intent.tool, result.error)

        self.state.update(last_tool=intent.tool, last_result_ok=result.ok)
        self._log(source, utterance, intent.tool, intent.args, result.ok,
                  result.error, result.duration_ms, result.data)

        if self.speaker is not None:
            self._maybe_speak(intent.tool, result)

        return AgentResult(result.ok, intent.tool, intent.args, result, duration_ms)

    # ── router miss → LLM planner ───────────────────────────────
    async def _handle_router_miss(self, utterance: str, source: str,
                                   started: float) -> AgentResult:
        if self.planner is None or not self.planner.is_available():
            # Honest error — tell the user exactly why, not just "I don't know".
            if self.planner is None:
                reason = ("I don't have a rule for that, and no LLM is wired. "
                          "Set LILY_LLM_PROVIDER=openrouter and LILY_LLM_API_KEY in .env "
                          "to enable general-purpose answers.")
                spoken = "I can't answer that yet — the language model isn't configured."
            else:
                reason = ("I don't have a rule for that, and my LLM key isn't set. "
                          "Add LILY_LLM_API_KEY to .env.")
                spoken = "I can't answer that — my language model key is missing."
            log.info("router miss (no llm): %r", utterance)
            self._log(source, utterance, None, None, False, reason, 0.0, None)
            if self.speaker is not None:
                self.speaker.speak(spoken)
            return AgentResult(False, None, {},
                               ToolResult.failure(reason),
                               (time.perf_counter() - started) * 1000,
                               reason=reason)

        log.info("router miss → LLM planner: %r", utterance)
        plan = await self.planner.plan_and_execute(utterance)
        duration_ms = (time.perf_counter() - started) * 1000

        # Direct answer (no tool)
        if plan.kind == "reply":
            self.state.update(last_tool="llm.reply", last_result_ok=True)
            self._log(source, utterance, "llm.reply", None, True, None,
                      duration_ms, {"reply": plan.reply})
            if self.speaker is not None and plan.reply:
                self.speaker.speak(plan.reply)
            return AgentResult(True, "llm.reply", {},
                               ToolResult.success(reply=plan.reply, spoken=plan.reply),
                               duration_ms)

        # One or more tool calls executed via the registry
        if plan.kind == "tools":
            first_ok = plan.tool_results[0].ok if plan.tool_results else False
            tool_name = plan.tool_calls[0][0] if plan.tool_calls else None
            tool_args = plan.tool_calls[0][1] if plan.tool_calls else {}
            self.state.update(last_tool=tool_name, last_result_ok=first_ok)
            # Log each executed call.
            for (name, args), res in zip(plan.tool_calls, plan.tool_results, strict=True):
                self._log(source, utterance, name, args, res.ok, res.error,
                          res.duration_ms, res.data)
            if self.speaker is not None and plan.reply:
                self.speaker.speak(plan.reply)
            aggregate_ok = all(r.ok for r in plan.tool_results)
            payload = ToolResult.success(
                reply=plan.reply, spoken=plan.reply,
                tool_results=[
                    {"tool": t, "args": a, "ok": r.ok, "data": r.data,
                     "error": r.error}
                    for (t, a), r in zip(plan.tool_calls, plan.tool_results, strict=True)
                ],
            )
            if not aggregate_ok:
                payload = ToolResult.failure(plan.reply or "tool failed",
                                             tool_results=payload.data.get("tool_results", []))
            return AgentResult(aggregate_ok, tool_name, tool_args, payload,
                               duration_ms)

        # Error path
        msg = plan.error or "the LLM couldn't answer"
        log.warning("planner error: %s", msg)
        self._log(source, utterance, "llm.error", None, False, msg, duration_ms, None)
        if self.speaker is not None:
            self.speaker.speak("sorry — I couldn't reach my brain right now")
        return AgentResult(False, None, {},
                           ToolResult.failure(msg),
                           duration_ms, reason=msg)

    # ── helpers ─────────────────────────────────────────────────
    def _log(self, source, utterance, tool, args, ok, error, duration_ms, data) -> None:
        try:
            self.memory.log_action(
                source=source, utterance=utterance, tool=tool, args=args,
                ok=ok, error=error, duration_ms=duration_ms, result=data,
            )
        except Exception:
            log.exception("memory log failed")

    def _maybe_speak(self, tool_name: str, result: ToolResult) -> None:
        if not self.settings.tts_enabled or self.speaker is None:
            return
        if not result.ok:
            self.speaker.speak(result.error or "that didn't work")
            return

        # If a tool provides its own spoken text, honor it directly.
        d = result.data
        if isinstance(d, dict) and d.get("spoken"):
            self.speaker.speak(str(d["spoken"]))
            return

        # Short spoken confirmations tuned per tool. Silent for chatty ones.
        if tool_name == "windows.volume_set" or tool_name == "windows.volume_adjust":
            self.speaker.speak(f"volume {d.get('volume')}")
        elif tool_name == "windows.mute":
            self.speaker.speak("muted")
        elif tool_name == "windows.unmute":
            self.speaker.speak("unmuted")
        elif tool_name == "apps.launch":
            self.speaker.speak(f"opening {d.get('launched') or 'app'}")
        elif tool_name == "apps.close":
            n = d.get("count", 0)
            self.speaker.speak("closed" if n else "nothing to close")
        elif tool_name == "windows.focus":
            self.speaker.speak("done")
        elif tool_name == "windows.screenshot":
            self.speaker.speak("screenshot saved")
        elif tool_name in {"windows.minimize", "windows.maximize", "windows.restore"}:
            self.speaker.speak("done")
        elif tool_name == "system.time":
            self.speaker.speak(f"it's {d.get('time', '')}")
        elif tool_name == "system.date":
            self.speaker.speak(f"today is {d.get('date', '')}")
        elif tool_name == "system.day":
            self.speaker.speak(f"it's {d.get('day', '')}")
