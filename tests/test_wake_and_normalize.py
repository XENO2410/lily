"""Wake-word extraction + STT text normalization tests.

These prove the transcript-cleanup path works without touching a microphone,
so I can verify the fix that killed 'This open node pad' before shipping it.
"""
from __future__ import annotations

import pytest

from lily.voice.normalize import (
    extract_wake_command,
    normalize_command,
    strip_filler,
)


# ── normalize_command ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("open note pad", "open notepad"),
    ("open Note Pad", "open notepad"),
    ("open vs code", "open vscode"),
    ("open visual studio code", "open vscode"),
    ("open you tube", "open youtube"),
    ("open command prompt", "open cmd"),
    ("open power shell", "open powershell"),
    ("Volume up.", "Volume up"),
    ("  extra   spaces   ", "extra spaces"),
])
def test_normalize_fixes_compounds(raw: str, expected: str) -> None:
    assert normalize_command(raw) == expected


def test_normalize_empty() -> None:
    assert normalize_command("") == ""


# ── extract_wake_command ─────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Lily, open notepad", "open notepad"),
    ("lily open notepad", "open notepad"),
    ("Hey Lily, open notepad", "open notepad"),
    ("Hey lily open notepad", "open notepad"),
    ("Hi Lily, open notepad", "open notepad"),
    ("OK Lily, open notepad", "open notepad"),
    ("Okay Lily open note pad", "open notepad"),
    ("Lilly, open notepad.", "open notepad"),           # STT often mishears
    ("Lily, open note pad", "open notepad"),
    # STT frequently mishears the L-sound. These should still count as Lily.
    ("Haley, what is the date today", "what is the date today"),
    ("Hey Haley, open notepad", "open notepad"),
    ("Riley, mute", "mute"),
    ("Hailey, volume up", "volume up"),
    ("Really open notepad", "open notepad"),           # 'Hey Lily' → 'Really'
    ("Millie, take a screenshot", "take a screenshot"),
])
def test_wake_extraction_hits(raw: str, expected: str) -> None:
    assert extract_wake_command(raw) == expected


@pytest.mark.parametrize("raw", [
    "open notepad",                    # no wake word
    "This open note pad.",             # the actual STT output that broke it
    "the notepad is open",             # discussing, not commanding
    "hey there, how are you",
    "",
])
def test_wake_extraction_misses(raw: str) -> None:
    assert extract_wake_command(raw) is None


def test_wake_word_alone() -> None:
    """Just 'Lily' with nothing after → empty string (heard but no command)."""
    assert extract_wake_command("Lily?") == ""
    assert extract_wake_command("Hey Lily") == ""


# ── strip_filler ─────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("um open notepad", "open notepad"),
    ("uh volume up", "volume up"),
    ("please open brave", "open brave"),
    ("this open notepad", "open notepad"),
    ("this is open notepad", "open notepad"),
    ("well close spotify", "close spotify"),
    ("open notepad", "open notepad"),   # no filler → unchanged
])
def test_strip_filler(raw: str, expected: str) -> None:
    assert strip_filler(raw) == expected
