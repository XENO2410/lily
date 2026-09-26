"""Voice listener: hotkey → mic capture → STT → agent dispatch.

Push-to-talk only for V0.1. Recording begins on hotkey press, stops on release,
the buffer is fed to the STT, and the resulting text is handed to the agent.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import numpy as np

from ..config import LilySettings
from ..logging import get_logger
from .hotkey import GlobalHotkey
from .stt import SpeechRecognizer
from .vad import trim_silence

log = get_logger("listener")

SAMPLE_RATE = 16000  # what faster-whisper expects


class VoiceListener:
    def __init__(
        self,
        settings: LilySettings,
        stt: SpeechRecognizer,
        on_utterance,  # callable(text) OR coroutine callable
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._s = settings
        self._stt = stt
        self._on_utterance = on_utterance
        self._loop = loop
        self._buffer: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()
        self._stream = None
        self._hotkey = GlobalHotkey(
            settings.hotkey,
            on_press=self._start_recording,
            on_release=self._stop_and_transcribe,
        )
        self._audio_available = False

    # ── lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        try:
            import sounddevice as sd
            self._audio_available = True
        except Exception as e:
            log.error("sounddevice unavailable: %s", e)
            log.error("voice mode disabled; use the console instead")
            return
        # Log the default input device so mic problems are diagnosable.
        try:
            default_in = sd.default.device[0]
            info = sd.query_devices(default_in) if default_in is not None else None
            if info:
                log.info("audio input: %s (device %s, %s ch, %d Hz native)",
                         info.get("name"), default_in,
                         info.get("max_input_channels"),
                         int(info.get("default_samplerate", 0)))
            else:
                log.warning("no default input device set in Windows Sound settings")
        except Exception:
            log.exception("could not query input device")
        self._hotkey.start()
        log.info("voice listener ready — hold %s to speak", self._s.hotkey)

    def stop(self) -> None:
        self._hotkey.stop()
        self._close_stream()

    # ── audio plumbing ──────────────────────────────────────────
    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_cb(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        with self._lock:
            if self._recording:
                # indata is float32 or int16 depending on dtype; we set int16 below
                self._buffer.append(indata.copy().reshape(-1))

    def _start_recording(self) -> None:
        if not self._audio_available:
            return
        with self._lock:
            if self._recording:
                return
            self._buffer.clear()
            self._recording = True
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                callback=self._audio_cb,
                blocksize=int(SAMPLE_RATE * 0.03),  # 30ms frames
            )
            self._stream.start()
            log.info("recording...")
        except Exception:
            log.exception("failed to start audio stream")
            with self._lock:
                self._recording = False

    def _stop_and_transcribe(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            frames = list(self._buffer)
            self._buffer.clear()
        self._close_stream()
        if not frames:
            log.info("no audio captured")
            return

        pcm = np.concatenate(frames).astype(np.int16)
        pcm = trim_silence(pcm, sample_rate=SAMPLE_RATE)
        duration_s = pcm.size / SAMPLE_RATE
        # RMS gives us a quick "was the mic actually picking anything up" signal.
        loudness = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if pcm.size else 0.0
        log.info("captured %.2fs (rms=%.0f)", duration_s, loudness)
        if duration_s < 0.25:
            log.info("too short, ignoring")
            return
        if loudness < 40:
            log.warning("audio was near-silent (rms=%.0f) — check the mic in Windows Sound settings", loudness)

        # Do STT on a worker thread; dispatch result via the event loop.
        threading.Thread(
            target=self._transcribe_and_dispatch,
            args=(pcm,),
            daemon=True,
        ).start()

    def _transcribe_and_dispatch(self, pcm: np.ndarray) -> None:
        try:
            text = self._stt.transcribe(pcm, sample_rate=SAMPLE_RATE)
        except Exception:
            log.exception("stt failed")
            return
        if not text:
            log.info("stt returned empty")
            return
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        callback = self._on_utterance
        if self._loop is None:
            try:
                callback(text)
            except Exception:
                log.exception("on_utterance callback failed")
            return
        # Schedule on the main loop. If it's a coroutine function, await it.
        def schedule() -> None:
            try:
                result: Any = callback(text)
                if asyncio.iscoroutine(result):
                    self._loop.create_task(result)
            except Exception:
                log.exception("on_utterance dispatch failed")
        self._loop.call_soon_threadsafe(schedule)
