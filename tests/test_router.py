"""Router / normalizer / arg-builder tests."""
from __future__ import annotations

import pytest

from lily.core.router import Router


@pytest.fixture
def router() -> Router:
    r = Router()
    r.add(r"^volume\s+up(?:\s+by\s+(\d{1,3}))?$", "windows.volume_adjust",
          lambda m: {"delta": int(m.group(1) or 10)})
    r.add(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*(?:%|percent)?$",
          "windows.volume_set", lambda m: {"level": int(m.group(1))})
    r.add(r"^mute$", "windows.mute")
    r.add(r"^(?:open|launch|start|run)\s+(.+)$", "apps.launch",
          lambda m: {"app": m.group(1).strip()})
    return r


def test_normalize_strips_address(router: Router) -> None:
    assert router.normalize("Lily, volume up") == "volume up"
    assert router.normalize("Hey Lily volume up") == "volume up"
    assert router.normalize("  LILY:  mute! ") == "mute"
    assert router.normalize("open notepad.") == "open notepad"


def test_normalize_strips_stt_filler(router: Router) -> None:
    # These are the exact kinds of STT outputs that broke live testing.
    assert router.normalize("This open note pad.") == "open note pad"
    assert router.normalize("um volume up") == "volume up"
    assert router.normalize("please open brave") == "open brave"


def test_starts_with_wake(router: Router) -> None:
    assert router.starts_with_wake("Lily, open notepad") is True
    assert router.starts_with_wake("Hey Lily open notepad") is True
    assert router.starts_with_wake("Lilly open notepad") is True  # STT typo
    assert router.starts_with_wake("open notepad") is False
    assert router.starts_with_wake("This is a test") is False
    assert router.starts_with_wake("") is False


def test_volume_up_default(router: Router) -> None:
    intent = router.match("Lily, volume up")
    assert intent is not None
    assert intent.tool == "windows.volume_adjust"
    assert intent.args == {"delta": 10}


def test_volume_up_by_5(router: Router) -> None:
    intent = router.match("volume up by 5")
    assert intent is not None
    assert intent.args == {"delta": 5}


@pytest.mark.parametrize("phrase,expected", [
    ("volume 25", 25),
    ("volume to 40", 40),
    ("set volume 60", 60),
    ("set volume to 100", 100),
    ("volume 50%", 50),
])
def test_volume_set_variants(router: Router, phrase: str, expected: int) -> None:
    intent = router.match(phrase)
    assert intent is not None
    assert intent.tool == "windows.volume_set"
    assert intent.args == {"level": expected}


def test_mute_no_args(router: Router) -> None:
    intent = router.match("mute")
    assert intent is not None
    assert intent.args == {}


def test_open_captures_app(router: Router) -> None:
    intent = router.match("open brave")
    assert intent is not None
    assert intent.tool == "apps.launch"
    assert intent.args == {"app": "brave"}


def test_no_match_returns_none(router: Router) -> None:
    assert router.match("compose a haiku about my code") is None
    # Time/date questions intentionally miss the router now — they route to LLM.
    assert router.match("what time is it") is None
    assert router.match("what's the date") is None


def test_help_and_capability_queries_route_to_system_help() -> None:
    """Users need a way to discover capabilities without an LLM.
    Directly maps to the 'expectations' finding from the Brill 2019 paper.
    """
    from lily.config import load_settings
    from lily.core.router import Router as R
    from lily.skills.windows import register_windows_skill
    from lily.skills.windows.skill import WindowsDeps
    from lily.tools.registry import ToolRegistry

    settings = load_settings()
    reg = ToolRegistry()
    r = R()
    register_windows_skill(reg, r, WindowsDeps(settings=settings))

    for phrase in [
        "help",
        "what can you do",
        "what are your skills",
        "list capabilities",
        "list your capabilities",
        "Lily, what can you do",
    ]:
        intent = r.match(phrase)
        assert intent is not None, f"help route should have matched {phrase!r}"
        assert intent.tool == "system.help"
