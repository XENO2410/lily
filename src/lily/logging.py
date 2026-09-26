"""Structured logging setup for Lily.

Console log for humans, JSONL action log for the SQLite indexer to consume later.
"""
from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path


def _reconfigure_stderr_utf8() -> None:
    """Windows console defaults to cp1252 which chokes on em-dashes, arrows,
    and characters like the ® in device names. Reconfigure to UTF-8 with
    fallback replacement so a single bad char never breaks a whole log line."""
    for stream in (sys.stderr, sys.stdout):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    _reconfigure_stderr_utf8()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "lily.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    return logging.getLogger("lily")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"lily.{name}")

