"""System tray icon. Right-click for menu, left-click shows status.

Runs on its own thread — pystray owns the event loop for the icon.
"""
from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

from ..logging import get_logger

log = get_logger("tray")


def _make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), color=(20, 20, 28))
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), fill=(140, 100, 220))
    d.text((22, 20), "L", fill=(255, 255, 255))
    return img


class Tray:
    def __init__(
        self,
        *,
        name: str = "Lily",
        on_status: Callable[[], str],
        on_toggle_voice: Callable[[], bool] | None = None,
        on_quit: Callable[[], None],
    ) -> None:
        self._name = name
        self._on_status = on_status
        self._on_toggle_voice = on_toggle_voice
        self._on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None

    def _build(self):
        import pystray

        def status(_icon, _item):
            try:
                _icon.notify(self._on_status(), self._name)
            except Exception:
                log.exception("status handler failed")

        def toggle_voice(icon, item):
            if self._on_toggle_voice is None:
                return
            try:
                new_state = self._on_toggle_voice()
                icon.notify("voice on" if new_state else "voice off", self._name)
            except Exception:
                log.exception("toggle voice failed")

        def quit_(icon, _item):
            try:
                self._on_quit()
            finally:
                icon.stop()

        menu_items = [pystray.MenuItem("Status", status)]
        if self._on_toggle_voice is not None:
            menu_items.append(pystray.MenuItem("Toggle voice", toggle_voice))
        menu_items.append(pystray.MenuItem("Quit", quit_))

        return pystray.Icon(
            self._name.lower(),
            icon=_make_icon_image(),
            title=self._name,
            menu=pystray.Menu(*menu_items),
        )

    def start(self) -> None:
        try:
            self._icon = self._build()
        except Exception:
            log.exception("tray init failed; skipping tray")
            return
        self._thread = threading.Thread(
            target=self._icon.run, name="lily-tray", daemon=True
        )
        self._thread.start()
        log.info("tray running")

    def stop(self) -> None:
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
