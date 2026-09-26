"""Post-STT text cleanup and wake-word extraction.

STT models often split compound tech words ("note pad" vs "notepad") and add
filler prefixes. This module normalizes those so the router matches cleanly.
"""
from __future__ import annotations

import re

# Common STT compound-word errors → canonical form.
# Order matters: longer phrases first so they win over shorter ones.
_COMPOUND_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bvisual\s+studio\s+code\b", re.IGNORECASE), "vscode"),
    (re.compile(r"\bvs\s+code\b", re.IGNORECASE), "vscode"),
    (re.compile(r"\bnote\s+pad\b", re.IGNORECASE), "notepad"),
    (re.compile(r"\byou\s+tube\b", re.IGNORECASE), "youtube"),
    (re.compile(r"\bwhat's\s+app\b", re.IGNORECASE), "whatsapp"),
    (re.compile(r"\bwhat\s+sap\b", re.IGNORECASE), "whatsapp"),
    (re.compile(r"\btask\s+manager\b", re.IGNORECASE), "task manager"),
    (re.compile(r"\bfile\s+explorer\b", re.IGNORECASE), "explorer"),
    (re.compile(r"\bcommand\s+prompt\b", re.IGNORECASE), "cmd"),
    (re.compile(r"\bpower\s+shell\b", re.IGNORECASE), "powershell"),
]

# STT often prepends filler / hallucinations. Strip a few common ones from the front.
_LEADING_FILLERS = re.compile(
    r"^\s*(?:um+|uh+|so|well|okay|ok|please|hey|hi|hello|this(?:\s+is)?|"
    r"i\s+want\s+to|can\s+you|could\s+you|would\s+you)\s+",
    re.IGNORECASE,
)

# STT often ends with a period or a superfluous word. Trim.
_TRAILING_JUNK = re.compile(r"[.!?,;: ]+$")

# Wake pattern: allow "hey lily", "lily", "lilly" (STT sometimes doubles the L),
# followed by any punctuation and optional whitespace.
# Also accept common whisper mishearings — 'Haley', 'Riley', 'Really' — because
# tiny.en / base.en fumble the L sound frequently, especially after "Hey".
_WAKE_MISHEARS: tuple[str, ...] = (
    "haley", "hayley", "hailey", "riley", "rylee",
    "really", "lily", "lilly", "lyly", "millie",
)


def _make_wake_pattern(word: str) -> re.Pattern[str]:
    stem = re.escape(word[:-1]) if word.endswith("y") else re.escape(word)
    # Match either the flexible stem+l*y+ form, OR any of the STT mishearings.
    alt = "|".join(re.escape(v) for v in _WAKE_MISHEARS)
    return re.compile(
        rf"^\s*(?:hey|hi|ok|okay)?\s*(?:{stem}l*y+|{alt})\b[\s,\.!:?-]*",
        re.IGNORECASE,
    )


def normalize_command(text: str) -> str:
    """Clean STT output into a form the router can match.

    - fixes compound-word errors ("note pad" → "notepad")
    - trims trailing punctuation
    - collapses whitespace
    """
    if not text:
        return ""
    out = text
    for pat, repl in _COMPOUND_FIXES:
        out = pat.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    out = _TRAILING_JUNK.sub("", out)
    return out


def extract_wake_command(text: str, wake_word: str = "lily") -> str | None:
    """If the text starts with a wake word, return the command after it. Else None.

    Accepts "Lily,", "Hey Lily", "Hi Lily", "OK Lily", "Lilly" (STT mishears).
    """
    if not text:
        return None
    normalized = normalize_command(text)
    if not normalized:
        return None
    pat = _make_wake_pattern(wake_word)
    m = pat.match(normalized)
    if not m:
        return None
    rest = normalized[m.end():].strip()
    # Also strip a leading filler after the wake word.
    rest = _LEADING_FILLERS.sub("", rest).strip()
    return rest if rest else ""


def strip_filler(text: str) -> str:
    """Remove common leading filler ('this', 'um', 'please')."""
    if not text:
        return text
    return _LEADING_FILLERS.sub("", text).strip()
