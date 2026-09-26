"""Keyboard primitives via pynput (V0.1: type + press hotkey).

Kept small on purpose — this is the low-level surface the UIA/vision layers will
build on later.
"""
from __future__ import annotations

from typing import Any

from ..logging import get_logger

log = get_logger("keyboard")


def type_text(text: str) -> dict[str, Any]:
    try:
        from pynput.keyboard import Controller  # type: ignore
    except ImportError as e:
        return {"ok": False, "error": f"pynput not installed: {e}"}
    try:
        Controller().type(text)
        return {"ok": True, "typed": len(text)}
    except Exception as e:
        log.exception("type_text failed")
        return {"ok": False, "error": str(e)}


def press_hotkey(combo: str) -> dict[str, Any]:
    """Press a hotkey combo like 'ctrl+alt+t' or '<ctrl>+<alt>+t'."""
    try:
        from pynput.keyboard import Controller, HotKey  # type: ignore
    except ImportError as e:
        return {"ok": False, "error": f"pynput not installed: {e}"}

    normalized = combo.lower().replace(" ", "")
    # HotKey.parse expects '<ctrl>+<alt>+t' form
    if "<" not in normalized:
        parts = normalized.split("+")
        specials = {"ctrl", "alt", "shift", "cmd", "win", "super"}
        normalized = "+".join(f"<{p}>" if p in specials else p for p in parts)

    try:
        keys = HotKey.parse(normalized)
        ctrl = Controller()
        for k in keys:
            ctrl.press(k)
        for k in reversed(keys):
            ctrl.release(k)
        return {"ok": True, "pressed": combo}
    except Exception as e:
        log.exception("press_hotkey failed")
        return {"ok": False, "error": str(e)}
