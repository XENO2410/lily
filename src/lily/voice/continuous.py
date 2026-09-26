"""Continuous voice listener with wake-word gate.

Always-on microphone → energy-based VAD → utterance chunking → STT →
wake-word filter → dispatch to agent.

Zero hotkey. Say "Lily, open notepad" — she wakes up on "Lily" and executes
"open notepad". Anything not addressed to Lily is transcribed, checked, and
silently discarded. Nothing is stored to disk.

CPU: near-zero during silence, ~5% during speech + brief STT burst.
"""
from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
from typing import Any

import numpy as np

from ..config import LilySettings
from ..logging import get_logger
from .normalize import extract_wake_command
from .stt import SpeechRecognizer

log = get_logger("continuous")

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)


class ContinuousListener:
    def __init__(
        self,
        settings: LilySettings,
        stt: SpeechRecognizer,
        on_utterance,
        loop: asyncio.AbstractEventLoop | None = None,
        on_speech_start=None,
        on_transcribed=None,
    ) -> None:
        self._s = settings
        self._stt = stt
        self._on_utterance = on_utterance
        self._loop = loop
        # Optional hooks the UI can plug into (e.g. show the orb).
        self._on_speech_start = on_speech_start
        self._on_transcribed = on_transcribed
        self._stream = None
        self._paused = False

        # VAD state (single-threaded, updated inside the audio callback)
        self._in_speech = False
        self._speech_frames: list[np.ndarray] = []
        self._silence_count = 0
        self._speech_count = 0
        self._baseline_rms = float(settings.wake_energy_floor)
        self._speech_threshold = self._baseline_rms * 2.5

        # Bounded queue so a slow STT can't blow memory during bursty speech.
        self._segment_q: queue.Queue = queue.Queue(maxsize=3)
        self._stop_flag = threading.Event()
        self._stt_thread: threading.Thread | None = None

    # ── lifecycle ───────────────────────────────────────────────
    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as e:
            log.error("sounddevice unavailable: %s — voice mode off", e)
            return

        try:
            default_in = sd.default.device[0]
            info = sd.query_devices(default_in) if default_in is not None else None
            if info:
                log.info("audio input: %s", info.get("name"))
        except Exception:
            log.exception("could not query audio device")

        self._stt_thread = threading.Thread(
            target=self._stt_worker, name="lily-stt", daemon=True
        )
        self._stt_thread.start()

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                callback=self._audio_cb,
                blocksize=FRAME_SAMPLES,
            )
            self._stream.start()
        except Exception:
            log.exception("failed to open microphone stream")
            return

        log.info("listening for '%s' — no hotkey needed. speak naturally.",
                 self._s.wake_word)

    def stop(self) -> None:
        self._stop_flag.set()
        with contextlib.suppress(queue.Full):
            self._segment_q.put_nowait(None)
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def pause(self) -> None:
        self._paused = True
        log.info("voice paused")

    def resume(self) -> None:
        self._paused = False
        log.info("voice resumed")

    # ── audio callback (runs on sounddevice thread; must be fast) ──
    def _audio_cb(self, indata, frames, _time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        if self._paused:
            return

        pcm = indata.reshape(-1)
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))

        # Adapt baseline slowly during silence so it tracks changing room noise.
        if not self._in_speech and rms < self._speech_threshold * 0.7:
            self._baseline_rms = 0.98 * self._baseline_rms + 0.02 * rms
            self._speech_threshold = max(
                float(self._s.wake_energy_floor), self._baseline_rms * 2.5
            )

        is_speech = rms > self._speech_threshold

        if is_speech:
            if not self._in_speech:
                self._in_speech = True
                self._speech_frames = []
                self._speech_count = 0
                if self._on_speech_start is not None:
                    try:
                        self._on_speech_start()
                    except Exception:
                        log.exception("on_speech_start crashed")
            self._speech_frames.append(pcm.copy())
            self._speech_count += 1
            self._silence_count = 0
        elif self._in_speech:
            # Keep a small trailing tail so we don't chop off word endings.
            self._speech_frames.append(pcm.copy())
            self._silence_count += 1
            if self._silence_count * FRAME_MS >= self._s.wake_silence_ms:
                self._endpoint_and_dispatch()

    def _endpoint_and_dispatch(self) -> None:
        min_frames = self._s.wake_min_speech_ms // FRAME_MS
        if self._speech_count < min_frames:
            # Too short — probably a cough or a keystroke.
            self._reset_speech()
            return

        segment = np.concatenate(self._speech_frames).astype(np.int16)
        self._reset_speech()
        try:
            self._segment_q.put_nowait(segment)
        except queue.Full:
            log.warning("stt busy — dropped a segment")

    def _reset_speech(self) -> None:
        self._in_speech = False
        self._speech_frames = []
        self._speech_count = 0
        self._silence_count = 0

    # ── STT worker thread ───────────────────────────────────────
    def _stt_worker(self) -> None:
        while not self._stop_flag.is_set():
            try:
                segment = self._segment_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if segment is None:
                break
            self._transcribe_and_maybe_dispatch(segment)

    def _transcribe_and_maybe_dispatch(self, pcm: np.ndarray) -> None:
        try:
            text = self._stt.transcribe(pcm, sample_rate=SAMPLE_RATE)
        except Exception:
            log.exception("stt failed")
            return
        if not text:
            if self._on_transcribed is not None:
                with contextlib.suppress(Exception):
                    self._on_transcribed(None, None)
            return

        command = extract_wake_command(text, wake_word=self._s.wake_word)
        if self._on_transcribed is not None:
            with contextlib.suppress(Exception):
                self._on_transcribed(text, command)

        if command is None:
            # INFO level so users can see when speech is heard but ignored —
            # otherwise it looks like Lily is broken when she's just being polite.
            log.info("heard (not for me): %r  — say 'Lily, ...' to address me",
                     text)
            return
        if not command:
            log.info("wake word heard with no command: %r", text)
            return

        log.info("→ command: %r", command)
        self._dispatch(command)

    def _dispatch(self, text: str) -> None:
        cb = self._on_utterance
        if self._loop is None:
            try:
                cb(text)
            except Exception:
                log.exception("on_utterance callback failed")
            return

        def schedule() -> None:
            try:
                result: Any = cb(text)
                if asyncio.iscoroutine(result):
                    self._loop.create_task(result)
            except Exception:
                log.exception("on_utterance dispatch failed")

        self._loop.call_soon_threadsafe(schedule)
