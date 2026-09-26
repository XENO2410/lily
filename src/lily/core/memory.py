"""Local SQLite store: action log + learning tables + user preferences.

Schema is designed for V0.1 but includes empty tables for the learning features
(playbooks, ui_selectors, aliases, macros, app_quirks) that Milestones V0.5+
will populate. This keeps future migrations painless.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..logging import get_logger

log = get_logger("memory")


SCHEMA = """
CREATE TABLE IF NOT EXISTS action_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    source        TEXT    NOT NULL,          -- 'console' | 'voice' | 'ipc' | 'macro'
    utterance     TEXT,
    tool          TEXT,
    args_json     TEXT,
    ok            INTEGER NOT NULL,
    error         TEXT,
    duration_ms   REAL    NOT NULL,
    result_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_log_ts ON action_log(ts DESC);

CREATE TABLE IF NOT EXISTS preferences (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase      TEXT NOT NULL,
    resolved    TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    created_at  REAL NOT NULL,
    UNIQUE(phrase, scope)
);

CREATE TABLE IF NOT EXISTS macros (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    triggers_json TEXT NOT NULL,
    steps_json   TEXT NOT NULL,
    uses         INTEGER NOT NULL DEFAULT 0,
    last_used    REAL,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playbooks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    utterance      TEXT NOT NULL,
    normalized     TEXT NOT NULL,
    plan_json      TEXT NOT NULL,
    uses           INTEGER NOT NULL DEFAULT 0,
    last_used      REAL,
    success_rate   REAL NOT NULL DEFAULT 1.0,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_playbooks_normalized ON playbooks(normalized);

CREATE TABLE IF NOT EXISTS ui_selectors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app             TEXT NOT NULL,
    role            TEXT NOT NULL,
    name_pattern    TEXT NOT NULL,
    selector_json   TEXT NOT NULL,
    verified_at     REAL NOT NULL,
    UNIQUE(app, role, name_pattern)
);

CREATE TABLE IF NOT EXISTS app_quirks (
    app         TEXT NOT NULL,
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    PRIMARY KEY(app, key)
);
"""


class Memory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._cursor() as cur:
            cur.executescript(SCHEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ── action log ────────────────────────────────────────────────
    def log_action(
        self,
        *,
        source: str,
        utterance: str | None,
        tool: str | None,
        args: dict[str, Any] | None,
        ok: bool,
        error: str | None,
        duration_ms: float,
        result: dict[str, Any] | None,
    ) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO action_log "
                    "(ts, source, utterance, tool, args_json, ok, error, duration_ms, result_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(), source, utterance, tool,
                        json.dumps(args) if args else None,
                        1 if ok else 0, error, duration_ms,
                        json.dumps(result) if result else None,
                    ),
                )
        except Exception:
            log.exception("failed to log action")

    def recent_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM action_log ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── inventory / privacy transparency ──────────────────────────
    def inventory(self) -> dict[str, int]:
        """Row counts per table — used by `lily privacy` to show what's stored."""
        counts: dict[str, int] = {}
        with self._cursor() as cur:
            for table in ("action_log", "preferences", "aliases",
                          "macros", "playbooks", "ui_selectors", "app_quirks"):
                try:
                    row = cur.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                    counts[table] = int(row["n"]) if row else 0
                except Exception:
                    counts[table] = 0
        return counts

    def forget_actions(self, keep_last: int = 0) -> int:
        """Delete action_log rows, optionally keeping the last N. Returns deleted count."""
        with self._cursor() as cur:
            if keep_last <= 0:
                deleted = cur.execute("SELECT COUNT(*) AS n FROM action_log").fetchone()["n"]
                cur.execute("DELETE FROM action_log")
            else:
                deleted = cur.execute(
                    "SELECT COUNT(*) AS n FROM action_log "
                    "WHERE id NOT IN (SELECT id FROM action_log "
                    "ORDER BY ts DESC, id DESC LIMIT ?)", (keep_last,)
                ).fetchone()["n"]
                cur.execute(
                    "DELETE FROM action_log WHERE id NOT IN "
                    "(SELECT id FROM action_log ORDER BY ts DESC, id DESC LIMIT ?)",
                    (keep_last,),
                )
            cur.execute("VACUUM")
        return int(deleted)

    # ── preferences ───────────────────────────────────────────────
    def get_pref(self, key: str, default: Any = None) -> Any:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT value_json FROM preferences WHERE key=?", (key,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_pref(self, key: str, value: Any) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO preferences(key, value_json, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), time.time()),
            )

    # ── aliases (populated V0.5+) ─────────────────────────────────
    def add_alias(self, phrase: str, resolved: str, scope: str = "global") -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO aliases(phrase, resolved, scope, created_at) "
                "VALUES(?, ?, ?, ?)",
                (phrase.lower(), resolved, scope, time.time()),
            )

    def resolve_alias(self, phrase: str, scope: str = "global") -> str | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT resolved FROM aliases WHERE phrase=? AND scope=?",
                (phrase.lower(), scope),
            ).fetchone()
        return row["resolved"] if row else None

    def close(self) -> None:
        self._conn.close()
