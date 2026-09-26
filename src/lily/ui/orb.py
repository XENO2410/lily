"""A small floating 'Hey Lily' orb — Siri-style visual feedback.

Borderless topmost window with a chroma-keyed transparent background and a
pulsing circle. No new dependencies: uses tkinter (stdlib) + Pillow (already in).

States:
    listening   blue,   pulsing         — after wake word detected
    thinking    purple, spinning ring   — during STT / tool execution
    speaking    green,  gentle glow     — while TTS is speaking
    error       orange, briefly shown
    hidden      window withdrawn

Thread safety: tkinter owns its mainloop on a dedicated thread. All public
methods marshal work back to that thread via root.after().
"""
from __future__ import annotations

import contextlib
import math
import threading
import time

from ..logging import get_logger

log = get_logger("orb")

# Chroma-key for the transparent parts of the window.
_MAGENTA = "#ff00ff"
_W, _H = 320, 130
_PANEL_COLOR = "#181a24"
_PANEL_BORDER = "#2c3040"
_TEXT_COLOR = "#e8eaf0"
_MUTED_COLOR = "#8a91a5"

_STATE_COLORS = {
    "listening": ("#4a90e2", "#7ec4ff"),
    "thinking":  ("#9b59b6", "#c78ee0"),
    "speaking":  ("#2ecc71", "#5be09a"),
    "error":     ("#e74c3c", "#ff8578"),
    "idle":      ("#4a90e2", "#7ec4ff"),
}


class Orb:
    def __init__(self, name: str = "Lily") -> None:
        self._name = name
        self._thread = threading.Thread(target=self._run, name="lily-orb", daemon=True)
        self._ready = threading.Event()
        self._root = None
        self._canvas = None
        self._orb_center = (_W // 2, 45)
        self._orb_id: int | None = None
        self._orb_glow_id: int | None = None
        self._title_id: int | None = None
        self._text_id: int | None = None
        self._state = "hidden"
        self._t0 = time.time()
        self._hide_after_id = None
        self._enabled = True

    # ── lifecycle ───────────────────────────────────────────────
    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            log.warning("orb: tk didn't initialize in 3s — continuing without orb UI")

    def stop(self) -> None:
        self._enabled = False
        if self._root is None:
            return
        with contextlib.suppress(Exception):
            self._root.after(0, self._root.destroy)
        # Give tk's mainloop a moment to exit before Python starts finalizing —
        # otherwise Tcl_AsyncDelete fires from the wrong thread on shutdown.
        self._thread.join(timeout=1.5)

    # ── public API (safe to call from any thread) ──────────────
    def show_listening(self) -> None:
        self._schedule(lambda: self._apply("listening",
                                            title="Listening…",
                                            text=f'say a command for {self._name}'))

    def show_transcript(self, text: str) -> None:
        text = (text or "").strip() or "…"
        self._schedule(lambda: self._apply("thinking",
                                            title=self._name,
                                            text=text))

    def show_thinking(self, text: str = "working…") -> None:
        self._schedule(lambda: self._apply("thinking",
                                            title=self._name,
                                            text=text))

    def show_speaking(self, text: str) -> None:
        text = (text or "").strip() or "done"
        self._schedule(lambda: self._apply("speaking",
                                            title=self._name,
                                            text=text))

    def show_error(self, text: str = "sorry — that didn't work") -> None:
        self._schedule(lambda: self._apply("error",
                                            title=self._name,
                                            text=text))

    def hide(self, delay_ms: int = 1500) -> None:
        self._schedule(lambda: self._schedule_hide(delay_ms))

    # ── tk thread ──────────────────────────────────────────────
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            log.exception("orb: tkinter unavailable")
            self._ready.set()
            return

        try:
            root = tk.Tk()
        except Exception:
            log.exception("orb: cannot create Tk root")
            self._ready.set()
            return

        self._root = root
        root.title(self._name)
        try:
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.96)
            root.configure(bg=_MAGENTA)
            root.attributes("-transparentcolor", _MAGENTA)
            root.wm_attributes("-toolwindow", True)
        except Exception:
            log.exception("orb: tk attribute setup failed")

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - _W) // 2
        y = sh - _H - 140  # sit above the taskbar
        root.geometry(f"{_W}x{_H}+{x}+{y}")

        self._canvas = tk.Canvas(root, width=_W, height=_H, bg=_MAGENTA,
                                 highlightthickness=0, bd=0)
        self._canvas.pack()

        self._draw_panel()
        self._draw_orb("idle")
        self._draw_labels()

        root.withdraw()
        self._ready.set()
        log.info("orb ready")

        # Start animation ticker (draws only when visible).
        self._tick()

        try:
            root.mainloop()
        except Exception:
            log.exception("orb: mainloop crashed")

    def _draw_panel(self) -> None:
        c = self._canvas
        # Rounded-rectangle background via smoothed polygon.
        pad = 4
        r = 20
        x1, y1, x2, y2 = pad, pad, _W - pad, _H - pad
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        c.create_polygon(pts, smooth=True, fill=_PANEL_COLOR, outline=_PANEL_BORDER, width=1)

    def _draw_orb(self, state: str) -> None:
        c = self._canvas
        cx, cy = self._orb_center
        core, glow = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
        # Outer glow
        self._orb_glow_id = c.create_oval(cx - 30, cy - 30, cx + 30, cy + 30,
                                          fill=glow, outline="")
        # Inner solid
        self._orb_id = c.create_oval(cx - 18, cy - 18, cx + 18, cy + 18,
                                     fill=core, outline="")

    def _draw_labels(self) -> None:
        c = self._canvas
        self._title_id = c.create_text(_W // 2, 88, text=self._name,
                                       fill=_TEXT_COLOR,
                                       font=("Segoe UI Semibold", 11))
        self._text_id = c.create_text(_W // 2, 108, text="",
                                      fill=_MUTED_COLOR,
                                      font=("Segoe UI", 9),
                                      width=_W - 30)

    # ── state / animation ─────────────────────────────────────
    def _apply(self, state: str, *, title: str, text: str) -> None:
        if self._root is None or self._canvas is None:
            return
        self._state = state
        core, glow = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
        try:
            self._canvas.itemconfig(self._orb_id, fill=core)
            self._canvas.itemconfig(self._orb_glow_id, fill=glow)
            self._canvas.itemconfig(self._title_id, text=title)
            self._canvas.itemconfig(self._text_id, text=text)
            if self._root.state() == "withdrawn":
                self._root.deiconify()
            if self._hide_after_id is not None:
                self._root.after_cancel(self._hide_after_id)
                self._hide_after_id = None
        except Exception:
            log.exception("orb: apply failed")

    def _schedule_hide(self, delay_ms: int) -> None:
        if self._root is None:
            return
        if self._hide_after_id is not None:
            with contextlib.suppress(Exception):
                self._root.after_cancel(self._hide_after_id)
        self._hide_after_id = self._root.after(delay_ms, self._do_hide)

    def _do_hide(self) -> None:
        if self._root is None:
            return
        self._state = "hidden"
        with contextlib.suppress(Exception):
            self._root.withdraw()

    def _tick(self) -> None:
        # Called every 40 ms. Cheap when hidden.
        if self._root is None or self._canvas is None:
            return
        try:
            if self._state != "hidden":
                self._animate()
            self._root.after(40, self._tick)
        except Exception:
            log.exception("orb tick failed")

    def _animate(self) -> None:
        cx, cy = self._orb_center
        t = time.time() - self._t0
        c = self._canvas
        try:
            if self._state == "listening":
                # Radius pulses 26..34, glow pulses 30..44
                r_in = 16 + math.sin(t * 6.0) * 4
                r_out = 30 + math.sin(t * 6.0) * 8
                c.coords(self._orb_id, cx - r_in, cy - r_in, cx + r_in, cy + r_in)
                c.coords(self._orb_glow_id, cx - r_out, cy - r_out, cx + r_out, cy + r_out)
            elif self._state == "thinking":
                # Wobbly ring
                r_in = 18 + math.sin(t * 12.0) * 2
                r_out = 26 + math.sin(t * 4.0) * 4
                c.coords(self._orb_id, cx - r_in, cy - r_in, cx + r_in, cy + r_in)
                c.coords(self._orb_glow_id, cx - r_out, cy - r_out, cx + r_out, cy + r_out)
            elif self._state == "speaking":
                # Bass-drum pulse in time with speech (~2 Hz)
                pulse = (math.sin(t * 12.0) + 1) * 0.5
                r_in = 18 + pulse * 6
                r_out = 32 + pulse * 10
                c.coords(self._orb_id, cx - r_in, cy - r_in, cx + r_in, cy + r_in)
                c.coords(self._orb_glow_id, cx - r_out, cy - r_out, cx + r_out, cy + r_out)
        except Exception:
            # Widget was probably destroyed — swallow.
            pass

    def _schedule(self, fn) -> None:
        if not self._enabled or self._root is None:
            return
        with contextlib.suppress(Exception):
            self._root.after(0, fn)
