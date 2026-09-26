"""Windows skill: apps, volume, window management, screenshot, clock.

Registers tools into the ToolRegistry and fast-path routes into the Router.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from ...computer import apps, keyboard, screen, volume, windows
from ...config import LilySettings
from ...core.router import Router
from ...logging import get_logger
from ...tools.base import NoArgs, PermissionLevel, Tool, ToolResult
from ...tools.registry import ToolRegistry

log = get_logger("skill.windows")


# ── Argument models ──────────────────────────────────────────────────
class AppArgs(BaseModel):
    app: str = Field(min_length=1, description="Application alias or name.")


class SetVolumeArgs(BaseModel):
    level: int = Field(ge=0, le=100)


class AdjustVolumeArgs(BaseModel):
    delta: int = Field(ge=-100, le=100)


class FocusArgs(BaseModel):
    query: str = Field(min_length=1)


class TypeArgs(BaseModel):
    text: str


class HotkeyArgs(BaseModel):
    combo: str


# ── Dependencies passed to handlers ──────────────────────────────────
@dataclass
class WindowsDeps:
    settings: LilySettings


def _wrap(result: dict) -> ToolResult:
    if result.get("ok") is False:
        return ToolResult.failure(result.get("error", "unknown error"),
                                  **{k: v for k, v in result.items()
                                     if k not in {"ok", "error"}})
    payload = {k: v for k, v in result.items() if k != "ok"}
    return ToolResult.success(**payload)


def register_windows_skill(registry: ToolRegistry, router: Router,
                           deps: WindowsDeps) -> None:
    s = deps.settings

    # ── tool handlers ────────────────────────────────────────────
    def _launch(a: AppArgs) -> ToolResult:
        try:
            r = apps.launch(a.app, default_browser=s.browser_default)
        except Exception as e:
            return ToolResult.failure(str(e), app=a.app)
        return ToolResult.success(**r)

    def _close(a: AppArgs) -> ToolResult:
        r = apps.close(a.app, default_browser=s.browser_default)
        return ToolResult.success(**r)

    def _set_volume(a: SetVolumeArgs) -> ToolResult:
        return _wrap(volume.set_volume(a.level))

    def _adjust_volume(a: AdjustVolumeArgs) -> ToolResult:
        return _wrap(volume.adjust(a.delta))

    def _mute(_: NoArgs) -> ToolResult:
        return _wrap(volume.mute())

    def _unmute(_: NoArgs) -> ToolResult:
        return _wrap(volume.unmute())

    def _minimize(_: NoArgs) -> ToolResult:
        return _wrap(windows.minimize_foreground())

    def _maximize(_: NoArgs) -> ToolResult:
        return _wrap(windows.maximize_foreground())

    def _restore(_: NoArgs) -> ToolResult:
        return _wrap(windows.restore_foreground())

    def _focus(a: FocusArgs) -> ToolResult:
        return _wrap(windows.focus(a.query))

    def _screenshot(_: NoArgs) -> ToolResult:
        return _wrap(screen.take_screenshot(log_dir=s.log_dir))

    def _foreground(_: NoArgs) -> ToolResult:
        return _wrap(windows.get_foreground())

    def _type(a: TypeArgs) -> ToolResult:
        return _wrap(keyboard.type_text(a.text))

    def _hotkey(a: HotkeyArgs) -> ToolResult:
        return _wrap(keyboard.press_hotkey(a.combo))

    # ── clock (pure stdlib, deterministic, instant) ──────────────
    def _time(_: NoArgs) -> ToolResult:
        now = datetime.now()
        return ToolResult.success(
            time=now.strftime("%I:%M %p").lstrip("0"),
            hour=now.hour, minute=now.minute,
        )

    def _date(_: NoArgs) -> ToolResult:
        now = datetime.now()
        return ToolResult.success(
            date=now.strftime("%A, %B %d, %Y"),
            iso=now.strftime("%Y-%m-%d"),
        )

    def _day(_: NoArgs) -> ToolResult:
        now = datetime.now()
        return ToolResult.success(day=now.strftime("%A"))

    # ── capability discovery (sets user expectations honestly) ───
    def _help(_: NoArgs) -> ToolResult:
        # Group registered tools by namespace and build a spoken summary.
        by_ns: dict[str, list[str]] = {}
        for t in registry.list_all():
            ns, _, _ = t.name.partition(".")
            by_ns.setdefault(ns, []).append(t.name)
        summary_lines: list[str] = []
        spoken_lines: list[str] = []
        for ns, names in sorted(by_ns.items()):
            summary_lines.append(f"{ns}: {', '.join(sorted(names))}")
            spoken_lines.append(ns)
        spoken = (
            "I can control apps, windows, volume, the mouse and keyboard, "
            "take screenshots, and answer general questions if the language model is on. "
            "Try: 'open notepad', 'volume 40', 'take a screenshot', or 'what time is it'."
        )
        return ToolResult.success(
            capabilities="\n".join(summary_lines),
            spoken=spoken,
            reply=spoken,
        )

    # ── register tools ───────────────────────────────────────────
    tools = [
        Tool("apps.launch", "Open an application by name.", AppArgs, _launch),
        Tool("apps.close", "Close all instances of an application.", AppArgs, _close,
             permission=PermissionLevel.SENSITIVE),
        Tool("windows.volume_set", "Set master volume 0-100.", SetVolumeArgs, _set_volume),
        Tool("windows.volume_adjust", "Change master volume by delta (-100..100).",
             AdjustVolumeArgs, _adjust_volume),
        Tool("windows.mute", "Mute master audio.", NoArgs, _mute),
        Tool("windows.unmute", "Unmute master audio.", NoArgs, _unmute),
        Tool("windows.minimize", "Minimize the foreground window.", NoArgs, _minimize),
        Tool("windows.maximize", "Maximize the foreground window.", NoArgs, _maximize),
        Tool("windows.restore", "Restore the foreground window.", NoArgs, _restore),
        Tool("windows.focus", "Bring a window matching the query to the foreground.",
             FocusArgs, _focus),
        Tool("windows.screenshot", "Capture all monitors as PNG.", NoArgs, _screenshot),
        Tool("windows.foreground", "Report the current foreground window.", NoArgs,
             _foreground),
        Tool("keyboard.type",
             "Type literal text into the currently focused window. "
             "IMPORTANT: focus the target window first with windows.focus if the "
             "correct app isn't already in front.",
             TypeArgs, _type),
        Tool("keyboard.hotkey",
             "Press a hotkey combo like 'ctrl+alt+t'. Note: system-level combos "
             "(win+l, ctrl+alt+delete) may not be interceptable.",
             HotkeyArgs, _hotkey),
        Tool("system.time", "Current local time.", NoArgs, _time),
        Tool("system.date", "Today's date.", NoArgs, _date),
        Tool("system.day", "Current day of the week.", NoArgs, _day),
        Tool("system.help", "List Lily's current capabilities.", NoArgs, _help),
    ]
    for t in tools:
        registry.register(t)

    # ── fast-path routes ────────────────────────────────────────
    # Volume — specific patterns first so they don't shadow each other.
    router.add(
        r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*(?:%|percent)?$",
        "windows.volume_set",
        lambda m: {"level": int(m.group(1))},
        label="volume set N",
    )
    router.add(
        r"^volume\s+up(?:\s+by\s+(\d{1,3}))?$",
        "windows.volume_adjust",
        lambda m: {"delta": int(m.group(1) or 10)},
        label="volume up",
    )
    router.add(
        r"^volume\s+down(?:\s+by\s+(\d{1,3}))?$",
        "windows.volume_adjust",
        lambda m: {"delta": -int(m.group(1) or 10)},
        label="volume down",
    )
    router.add(
        r"^(?:turn\s+it\s+)?(?:mute|silence|shut\s+up)$",
        "windows.mute",
        label="mute",
    )
    router.add(
        r"^unmute(?:\s+audio)?$",
        "windows.unmute",
        label="unmute",
    )

    # Clock — no router rules. LLM planner calls the system.* tools directly
    # so it can also handle "what time is it in Tokyo", "days until Christmas", etc.

    # Window management
    router.add(r"^minimi[sz]e(?:\s+window)?$", "windows.minimize", label="minimize")
    router.add(r"^maximi[sz]e(?:\s+window)?$", "windows.maximize", label="maximize")
    router.add(r"^restore(?:\s+window)?$", "windows.restore", label="restore")
    router.add(
        r"^(?:switch\s+to|focus|bring\s+up|show)\s+(.+)$",
        "windows.focus",
        lambda m: {"query": m.group(1).strip()},
        label="focus X",
    )
    router.add(
        r"^(?:take\s+a\s+)?screenshot$|^capture\s+(?:the\s+)?screen$",
        "windows.screenshot",
        label="screenshot",
    )
    router.add(
        r"^what(?:'s|\s+is)\s+(?:on\s+screen|active|the\s+active\s+window)$",
        "windows.foreground",
        label="foreground query",
    )

    # Apps
    router.add(
        r"^(?:open|launch|start|run)\s+(.+)$",
        "apps.launch",
        lambda m: {"app": _clean_app_name(m.group(1))},
        label="open X",
    )
    router.add(
        r"^(?:close|quit|exit|kill)\s+(.+)$",
        "apps.close",
        lambda m: {"app": _clean_app_name(m.group(1))},
        label="close X",
    )

    # Help / capability discovery — hardcoded so it works even without LLM.
    router.add(
        r"^(?:help|what\s+can\s+you\s+do|what\s+are\s+your\s+skills|"
        r"list\s+(?:your\s+)?capabilities|what\s+do\s+you\s+know)$",
        "system.help",
        label="help",
    )

    log.info("registered windows skill: %d tools, router now has %d routes",
             len(tools), router.size())


_TRAILING_APP_JUNK = re.compile(r"\s+(please|now|for\s+me|app|application)$", re.IGNORECASE)

# STT compound-word errors on app names → canonical alias key.
_APP_NAME_FIXES: dict[str, str] = {
    "note pad": "notepad",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "you tube": "youtube",
    "task manager": "task_manager",
    "file explorer": "explorer",
    "google chrome": "chrome",
    "microsoft edge": "edge",
    "command prompt": "cmd",
    "power shell": "powershell",
    "windows terminal": "terminal",
}


def _clean_app_name(raw: str) -> str:
    """Normalize captured app names for the launcher.

    - strip trailing 'please' / 'app' / punctuation
    - drop 'the ' prefix
    - collapse STT compound errors ('note pad' → 'notepad')
    """
    s = raw.strip().rstrip(".!?,")
    s = _TRAILING_APP_JUNK.sub("", s)
    if s.lower().startswith("the "):
        s = s[4:]
    lower = s.lower()
    if lower in _APP_NAME_FIXES:
        return _APP_NAME_FIXES[lower]
    return s
