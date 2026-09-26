"""Global push-to-talk hotkey via pynput.

Fires on_press when the full combo becomes active, on_release when any key of
the combo goes up. Combo strings accept pynput's `<ctrl>+<alt>+<space>` or the
shorter `ctrl+alt+space` form.
"""
from __future__ import annotations

from collections.abc import Callable

from ..logging import get_logger

log = get_logger("hotkey")


def _normalize(combo: str) -> str:
    s = combo.strip().lower().replace(" ", "")
    if "<" in s:
        return s
    parts = s.split("+")
    specials = {"ctrl", "alt", "shift", "cmd", "win", "super"}
    return "+".join(f"<{p}>" if p in specials else p for p in parts)


class GlobalHotkey:
    """Push-to-talk listener. Runs the pynput hook on its own daemon thread."""

    def __init__(
        self,
        combo: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._combo_str = _normalize(combo)
        self._on_press = on_press
        self._on_release = on_release
        self._listener = None
        self._active = False
        self._pressed: set = set()
        self._target: set = set()

    def start(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
        except ImportError as e:
            log.error("pynput unavailable: %s", e)
            return

        self._target = set(keyboard.HotKey.parse(self._combo_str))

        def canonical(k):
            return self._listener.canonical(k) if self._listener else k

        def on_press(key):
            k = canonical(key)
            if k in self._target:
                self._pressed.add(k)
            if self._target.issubset(self._pressed) and not self._active:
                self._active = True
                log.info("hotkey PRESSED")
                try:
                    self._on_press()
                except Exception:
                    log.exception("hotkey on_press handler crashed")

        def on_release(key):
            k = canonical(key)
            was_active = self._active
            self._pressed.discard(k)
            if was_active and not self._target.issubset(self._pressed):
                self._active = False
                log.info("hotkey RELEASED")
                try:
                    self._on_release()
                except Exception:
                    log.exception("hotkey on_release handler crashed")

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info("global hotkey armed: %s", self._combo_str)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
