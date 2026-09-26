"""Memory / SQLite tests — schema, action log, preferences, aliases."""
from __future__ import annotations

from pathlib import Path

import pytest

from lily.core.memory import Memory


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    m = Memory(tmp_path / "lily.sqlite3")
    yield m
    m.close()


def test_schema_creates_all_tables(mem: Memory, tmp_path: Path) -> None:
    # If schema didn't run, log_action would fail below.
    mem.log_action(
        source="test", utterance="hi", tool=None, args=None,
        ok=True, error=None, duration_ms=1.0, result={"note": "ok"},
    )
    assert (tmp_path / "lily.sqlite3").exists()


def test_recent_actions_returns_newest_first(mem: Memory) -> None:
    for i in range(3):
        mem.log_action(
            source="test", utterance=f"cmd {i}", tool="t", args={"i": i},
            ok=True, error=None, duration_ms=float(i), result=None,
        )
    rows = mem.recent_actions(limit=10)
    assert [r["utterance"] for r in rows] == ["cmd 2", "cmd 1", "cmd 0"]


def test_preferences_roundtrip(mem: Memory) -> None:
    assert mem.get_pref("missing") is None
    mem.set_pref("browser", "brave")
    mem.set_pref("workspace", {"projects": ["banking"]})
    assert mem.get_pref("browser") == "brave"
    assert mem.get_pref("workspace") == {"projects": ["banking"]}


def test_alias_resolve(mem: Memory) -> None:
    assert mem.resolve_alias("work profile") is None
    mem.add_alias("work profile", "chrome-profile-2")
    assert mem.resolve_alias("Work Profile") == "chrome-profile-2"


def test_inventory_reports_all_tables(mem: Memory) -> None:
    inv = mem.inventory()
    for table in ("action_log", "preferences", "aliases",
                  "macros", "playbooks", "ui_selectors", "app_quirks"):
        assert table in inv
        assert inv[table] == 0

    mem.log_action(source="t", utterance="u", tool=None, args=None,
                   ok=True, error=None, duration_ms=1.0, result=None)
    mem.set_pref("theme", "dark")
    inv = mem.inventory()
    assert inv["action_log"] == 1
    assert inv["preferences"] == 1


def test_forget_all(mem: Memory) -> None:
    for i in range(5):
        mem.log_action(source="t", utterance=f"cmd {i}", tool="t", args=None,
                       ok=True, error=None, duration_ms=1.0, result=None)
    deleted = mem.forget_actions(keep_last=0)
    assert deleted == 5
    assert mem.recent_actions() == []


def test_forget_keeps_last_n(mem: Memory) -> None:
    for i in range(5):
        mem.log_action(source="t", utterance=f"cmd {i}", tool="t", args=None,
                       ok=True, error=None, duration_ms=float(i), result=None)
    deleted = mem.forget_actions(keep_last=2)
    assert deleted == 3
    remaining = mem.recent_actions()
    assert len(remaining) == 2
    # The two most recent survive.
    assert {r["utterance"] for r in remaining} == {"cmd 3", "cmd 4"}
