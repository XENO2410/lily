"""Window management: enumerate, minimize/maximize/restore, focus by title match.

Uses pywin32 (win32gui / win32process / win32con). Falls back gracefully on non-Windows.
"""
from __future__ import annotations

import sys
from typing import Any

import psutil

from ..logging import get_logger

log = get_logger("windows")

_available = sys.platform == "win32"

if _available:  # pragma: no cover - platform specific
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as e:
        _available = False
        log.warning("pywin32 unavailable: %s", e)


def is_available() -> bool:
    return _available


def _proc_name(hwnd: int) -> str:
    if not _available:
        return ""
    try:  # pragma: no cover
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name()
    except Exception:
        return ""


def get_foreground() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "windowing unavailable"}
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "error": "no foreground window"}
    title = win32gui.GetWindowText(hwnd)
    return {"ok": True, "hwnd": int(hwnd), "title": title, "process": _proc_name(hwnd)}


def minimize_foreground() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "windowing unavailable"}
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "error": "no foreground window"}
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    return {"ok": True, "action": "minimize"}


def maximize_foreground() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "windowing unavailable"}
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "error": "no foreground window"}
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    return {"ok": True, "action": "maximize"}


def restore_foreground() -> dict[str, Any]:
    if not _available:
        return {"ok": False, "error": "windowing unavailable"}
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "error": "no foreground window"}
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    return {"ok": True, "action": "restore"}


def list_windows() -> list[dict[str, Any]]:
    if not _available:
        return []
    windows: list[dict[str, Any]] = []

    def _enum(hwnd: int, _lparam: int) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        windows.append({
            "hwnd": int(hwnd),
            "title": title,
            "process": _proc_name(hwnd),
        })
        return True

    win32gui.EnumWindows(_enum, 0)
    return windows


def focus(query: str) -> dict[str, Any]:
    """Bring the first window whose title/process contains `query` to the foreground."""
    if not _available:
        return {"ok": False, "error": "windowing unavailable"}
    q = query.strip().lower()
    if not q:
        return {"ok": False, "error": "empty query"}

    best: dict[str, Any] | None = None
    for w in list_windows():
        title = w["title"].lower()
        proc = (w["process"] or "").lower()
        if q in title or q in proc or q == proc.replace(".exe", ""):
            best = w
            break

    if best is None:
        return {"ok": False, "error": f"no window matching {query!r}"}

    hwnd = best["hwnd"]
    try:  # pragma: no cover
        # Restore if minimized before focusing.
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        # SetForegroundWindow is async on Windows — give it a beat to settle so
        # a subsequent keyboard.type call lands in the right window.
        import time as _time
        _time.sleep(0.12)
    except Exception as e:
        return {"ok": False, "error": f"could not focus: {e}", "title": best["title"]}

    return {"ok": True, "focused": best["title"], "process": best["process"]}
