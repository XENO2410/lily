"""Fast intent router — pattern → typed tool call, zero LLM in the path.

Every skill contributes routes via router.add(). Matching is O(n) over compiled
regexes, which is fine for the V0.1 tool count (~15).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from re import Pattern
from typing import Any

from ..logging import get_logger

log = get_logger("router")

# Strip common addressing prefixes so "Lily, volume up" == "volume up".
# Accepts: "lily", "lilly" (STT often doubles), optional "hey|hi|ok|okay" prefix,
# optional trailing comma/period/hyphen.
_ADDRESS_RE = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay)\s+)?lil+y+\b[\s,\.:!?-]*",
    re.IGNORECASE,
)

# STT filler that shows up at the start of transcripts.
_LEADING_FILLER = re.compile(
    r"^\s*(?:um+|uh+|so|well|please|this(?:\s+is)?)\s+",
    re.IGNORECASE,
)


@dataclass
class Intent:
    tool: str
    args: dict[str, Any]
    source_pattern: str


ArgsBuilder = Callable[[re.Match[str]], dict[str, Any]]


@dataclass
class Route:
    pattern: Pattern[str]
    tool: str
    args_builder: ArgsBuilder
    label: str


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(
        self,
        pattern: str,
        tool: str,
        args_builder: ArgsBuilder | None = None,
        *,
        label: str | None = None,
    ) -> None:
        compiled = re.compile(pattern, re.IGNORECASE)
        self._routes.append(
            Route(
                pattern=compiled,
                tool=tool,
                args_builder=args_builder or (lambda _m: {}),
                label=label or pattern,
            )
        )

    def normalize(self, utterance: str) -> str:
        stripped = _ADDRESS_RE.sub("", utterance).strip()
        stripped = _LEADING_FILLER.sub("", stripped).strip()
        stripped = re.sub(r"\s+", " ", stripped)
        stripped = stripped.rstrip(".!?,;: ")
        return stripped

    def starts_with_wake(self, utterance: str) -> bool:
        return bool(_ADDRESS_RE.match(utterance or ""))

    def match(self, utterance: str) -> Intent | None:
        text = self.normalize(utterance)
        for route in self._routes:
            m = route.pattern.match(text)
            if m:
                args = route.args_builder(m)
                log.debug("router hit: %r → %s(%s)", text, route.tool, args)
                return Intent(tool=route.tool, args=args, source_pattern=route.label)
        return None

    def size(self) -> int:
        return len(self._routes)
