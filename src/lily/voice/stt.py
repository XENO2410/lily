"""faster-whisper STT: warm-loaded at daemon startup, held resident.

Kept behind a Protocol so tests / future backends can substitute.
"""
from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy as np

from ..config import LilySettings
from ..logging import get_logger
from .normalize import normalize_command

log = get_logger("stt")


class SpeechRecognizer(Protocol):
    def transcribe(self, pcm16: np.ndarray, sample_rate: int = 16000) -> str: ...
    def preload(self) -> None: ...
    def is_ready(self) -> bool: ...
    def unload(self) -> None: ...


class FasterWhisperSTT:
    def __init__(self, settings: LilySettings) -> None:
        self._s = settings
        self._model = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._ttl_timer: threading.Timer | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # heavy import kept lazy
        device = self._s.stt_device
        compute = self._s.stt_compute_type
        if device == "auto":
            device, compute = "cpu", "int8"
        log.info("loading whisper %s on %s/%s (this can take a minute on first run)",
                 self._s.stt_model, device, compute)
        started = time.perf_counter()
        self._model = WhisperModel(
            self._s.stt_model, device=device, compute_type=compute
        )
        log.info("whisper ready in %.1fs", time.perf_counter() - started)

    def preload(self) -> None:
        """Load the model now (blocking). Call at daemon startup so first
        command doesn't pay the cold-start cost."""
        with self._lock:
            self._ensure_loaded()
            self._last_used = time.time()

    def is_ready(self) -> bool:
        return self._model is not None

    def _schedule_unload(self) -> None:
        ttl = self._s.stt_warm_ttl_s
        if ttl <= 0:
            return
        if self._ttl_timer is not None:
            self._ttl_timer.cancel()
        self._ttl_timer = threading.Timer(ttl, self._maybe_unload)
        self._ttl_timer.daemon = True
        self._ttl_timer.start()

    def _maybe_unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            if time.time() - self._last_used >= self._s.stt_warm_ttl_s:
                log.info("unloading whisper (idle > %ds)", self._s.stt_warm_ttl_s)
                self._model = None

    def transcribe(self, pcm16: np.ndarray, sample_rate: int = 16000) -> str:
        with self._lock:
            self._ensure_loaded()
            self._last_used = time.time()

        audio = pcm16.astype(np.float32) / 32768.0
        if sample_rate != 16000:
            new_len = int(audio.size * 16000 / sample_rate)
            audio = np.interp(
                np.linspace(0, audio.size, new_len, endpoint=False),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)

        started = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio,
            language=self._s.stt_language,
            vad_filter=False,
            beam_size=1,
            no_speech_threshold=0.5,
            condition_on_previous_text=False,
            initial_prompt=self._s.stt_initial_prompt or None,
            temperature=0.0,
        )
        raw = " ".join(seg.text.strip() for seg in segments).strip()
        text = normalize_command(raw)
        log.info("stt %.0fms → %r", (time.perf_counter() - started) * 1000, text)
        self._schedule_unload()
        return text

    def unload(self) -> None:
        with self._lock:
            self._model = None
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None

