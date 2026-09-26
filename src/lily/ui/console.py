"""Typed command console — the fastest way to try Lily without voice.

Reads lines from stdin, hands them to the Agent, prints a compact result.
"""
from __future__ import annotations

import asyncio

from ..core.agent import Agent
from ..logging import get_logger

log = get_logger("console")

_BANNER = (
    "\nLily console — type a command, or 'exit' to quit.\n"
    "Try: open notepad, volume 25, minimize, screenshot, tools, help\n"
)


def _fmt(agent_result) -> str:
    if agent_result.tool is None:
        return f"?? {agent_result.reason or 'no match'}"
    prefix = "OK " if agent_result.result.ok else "ERR"
    payload = agent_result.result.data if agent_result.result.ok else agent_result.result.error
    return f"{prefix} ({agent_result.duration_ms:.0f}ms) {payload}"


async def run_console(agent: Agent, *, stop_event: asyncio.Event | None = None) -> None:
    print(_BANNER)
    loop = asyncio.get_running_loop()

    while stop_event is None or not stop_event.is_set():
        try:
            line = await loop.run_in_executor(None, _read_prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if line is None:
            break
        raw = line.strip()
        if not raw:
            continue
        if raw.lower() in {"exit", "quit", ":q"}:
            break
        if raw.lower() in {"help", "?"}:
            _print_help()
            continue
        if raw.lower() == "tools":
            for t in sorted(agent.registry.list_all(), key=lambda x: x.name):
                print(f"  {t.name:<28} [{t.permission.value}]  {t.description}")
            continue
        if raw.lower() == "state":
            print(agent.state.snapshot())
            continue

        try:
            result = await agent.handle(raw, source="console")
            print(_fmt(result))
        except Exception:
            log.exception("console dispatch failed")
            print("ERR internal error — see logs/lily.log")


def _read_prompt() -> str | None:
    try:
        return input("> ")
    except EOFError:
        return None


def _print_help() -> None:
    print(
        "\nBuilt-in commands:\n"
        "  tools           list registered tools\n"
        "  state           show ComputerState snapshot\n"
        "  help | ?        this message\n"
        "  exit | quit     leave the console\n"
        "\nExample utterances:\n"
        "  open brave        launch brave\n"
        "  close spotify     terminate spotify\n"
        "  volume 40         set master volume\n"
        "  volume up by 5    adjust master volume\n"
        "  mute / unmute\n"
        "  minimize / maximize / restore\n"
        "  switch to code    focus a window whose title contains 'code'\n"
        "  screenshot        save PNG of all monitors\n"
    )


# Simple synchronous entry (used by `lily do` when no daemon is running).
def run_console_sync(agent: Agent) -> None:
    asyncio.run(run_console(agent))


# Utility for one-shot from CLI.
def parse_cli_utterance(argv: list[str]) -> str:
    """Join argv into a single utterance. argparse already split words; we just re-join."""
    return " ".join(argv).strip()
