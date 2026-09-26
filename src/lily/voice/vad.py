"""Simple energy-based voice activity detection.

V0.1 only uses this to trim trailing silence from a PTT recording — the model is
already given the whole clip. Silero VAD (V0.2) will do endpointing for
always-on / wake-word modes.
"""
from __future__ import annotations

import numpy as np


def rms(pcm16: np.ndarray) -> float:
    if pcm16.size == 0:
        return 0.0
    x = pcm16.astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))


def trim_silence(
    pcm16: np.ndarray,
    sample_rate: int = 16000,
    frame_ms: int = 30,
    threshold: float = 250.0,
    trailing_ms: int = 300,
) -> np.ndarray:
    """Chop trailing silence longer than trailing_ms. Returns a view or copy."""
    if pcm16.size == 0:
        return pcm16
    frame = int(sample_rate * frame_ms / 1000)
    if frame <= 0:
        return pcm16

    # walk from the end, find last frame above threshold
    n = pcm16.size // frame
    if n == 0:
        return pcm16
    last_voiced = -1
    for i in range(n - 1, -1, -1):
        seg = pcm16[i * frame:(i + 1) * frame]
        if rms(seg) > threshold:
            last_voiced = i
            break
    if last_voiced < 0:
        return pcm16
    keep_frames = last_voiced + 1 + max(0, trailing_ms // frame_ms)
    return pcm16[: keep_frames * frame]
