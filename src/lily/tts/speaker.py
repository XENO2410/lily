"""Lily's voice — text-to-speech.

V0.1 backend: pyttsx3 (Windows SAPI, no downloads, no models).
Voice quality is basic but the pipeline is real; piper backend can drop in later
by implementing the same Speaker.speak(text) contract.

All backends push text through a background worker so `speak()` never blocks the
agent thread.
"""
from __future__ import annotations

import contextlib
import queue
import sys
import threading
from typing import Protocol

from ..config import LilySettings
from ..logging import get_logger

log = get_logger("tts")


def _win_com_init() -> bool:
    """CoInitialize this thread for SAPI/comtypes. No-op off Windows."""
    if sys.platform != "win32":
        return False
    try:
        import pythoncom  # from pywin32
        pythoncom.CoInitialize()
        return True
    except Exception:
        log.exception("tts: CoInitialize failed")
        return False


def _win_com_uninit() -> None:
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import pythoncom
        pythoncom.CoUninitialize()


class Speaker(Protocol):
    def speak(self, text: str) -> None: ...
    def stop(self) -> None: ...


class _NullSpeaker:
    def speak(self, text: str) -> None:  # noqa: D401
        log.debug("(tts disabled) %s", text)

    def stop(self) -> None:
        pass


class _Pyttsx3Speaker:
    """SAPI-backed. Runs the engine on a dedicated thread; enqueue-only from callers."""

    _SENTINEL = object()

    def __init__(self, voice: str = "", rate: int = 190,
                 gender: str = "female") -> None:
        self._voice = voice
        self._rate = rate
        self._gender = (gender or "any").lower()
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="lily-tts", daemon=True)
        self._thread.start()

    def _init_engine(self):
        import pyttsx3  # imported inside the worker thread on purpose
        engine = pyttsx3.init()
        with contextlib.suppress(Exception):
            engine.setProperty("rate", int(self._rate))
        picked = self._pick_voice(engine)
        if picked is not None:
            with contextlib.suppress(Exception):
                engine.setProperty("voice", picked.id)
                log.info("tts voice: %s", picked.name)
        return engine

    def _pick_voice(self, engine):
        try:
            voices = engine.getProperty("voices") or []
        except Exception:
            return None
        if not voices:
            return None

        # 1) explicit name match wins
        if self._voice:
            for v in voices:
                if self._voice.lower() in (v.name or "").lower():
                    return v

        # 2) gender-based selection
        female_hints = ("zira", "hazel", "susan", "linda", "eva",
                        "catherine", "aria", "jenny", "sonia", "female")
        male_hints = ("david", "mark", "james", "george", "guy", "male")

        if self._gender in {"female", "male"}:
            hints = female_hints if self._gender == "female" else male_hints
            # Try SAPI's gender attribute (present on Windows).
            for v in voices:
                g = (getattr(v, "gender", "") or "").lower()
                if g == self._gender:
                    return v
            # Fallback: substring match on voice name.
            for v in voices:
                name = (v.name or "").lower()
                if any(h in name for h in hints):
                    return v

        # 3) whatever's default
        return voices[0]

    def _run(self) -> None:
        # SAPI's comtypes driver requires COM in this thread. Without this the
        # first CreateObject("SAPI.SpVoice") fails with "CoInitialize has not
        # been called" and comtypes.gen.SpeechLib never gets generated.
        com_ok = _win_com_init()
        try:
            engine = self._init_engine()
        except Exception:
            log.exception("tts engine init failed; falling silent")
            if com_ok:
                _win_com_uninit()
            return

        try:
            while True:
                item = self._queue.get()
                if item is self._SENTINEL:
                    break
                text = str(item)
                if not text:
                    continue
                try:
                    engine.say(text)
                    engine.runAndWait()
                except RuntimeError:
                    # Known pyttsx3 quirk: loop already running. Re-init and retry once.
                    try:
                        engine = self._init_engine()
                        engine.say(text)
                        engine.runAndWait()
                    except Exception:
                        log.exception("tts speak retry failed")
                except Exception:
                    log.exception("tts speak failed")
        finally:
            if com_ok:
                _win_com_uninit()

    def speak(self, text: str) -> None:
        if not text:
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            log.warning("tts queue full, dropping: %s", text)

    def stop(self) -> None:
        self._queue.put(self._SENTINEL)


def make_speaker(settings: LilySettings) -> Speaker:
    if not settings.tts_enabled or settings.tts_engine == "none":
        return _NullSpeaker()
    if settings.tts_engine == "pyttsx3":
        try:
            return _Pyttsx3Speaker(
                voice=settings.tts_voice,
                rate=settings.tts_rate,
                gender=settings.tts_gender,
            )
        except Exception:
            log.exception("pyttsx3 init failed; using null speaker")
            return _NullSpeaker()
    if settings.tts_engine == "piper":
        # V0.2 — needs binary + voice model download.
        log.warning("piper backend not implemented yet, using null speaker")
        return _NullSpeaker()
    return _NullSpeaker()
