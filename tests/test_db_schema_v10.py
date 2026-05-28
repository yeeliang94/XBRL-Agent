"""DB migration v9 → v10: `runs.orchestration` (TEXT DEFAULT 'split').

Mirrors the pinning shape of `test_db_schema_v9.py` (and earlier
migrations): fresh init has the column with the right default, a v9
fixture upgrades cleanly, and re-init is idempotent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]  # name → type
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v10():
    assert CURRENT_SCHEMA_VERSION >= 10


def test_fresh_init_has_orchestration_column_with_split_default(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        cols = _table_columns(conn, "runs")
        assert "orchestration" in cols, "v10 column missing on fresh init"
        # Insert a row without specifying orchestration — default should be 'split'.
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES (?, ?, ?)",
            ("2026-05-28T00:00:00Z", "x.pdf", "completed"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT orchestration FROM runs LIMIT 1"
        ).fetchone()
        assert row[0] == "split"
        # Schema version marker is v10 (or later).
        assert _schema_version(conn) >= 10
    finally:
        conn.close()


def test_v9_fixture_upgrades_cleanly(tmp_path):
    db = tmp_path / "v9.db"
    # Hand-build a v9 schema: the runs table without `orchestration`.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (9)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) VALUES (?, ?, ?)",
            ("2026-05-01T00:00:00Z", "legacy.pdf", "completed"),
        )
        conn.commit()
    finally:
        conn.close()

    # Walk forward.
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        cols = _table_columns(conn, "runs")
        assert "orchestration" in cols
        # Pre-existing row: SQLite ALTER ADD COLUMN initialises with the
        # column default ('split').
        row = conn.execute(
            "SELECT orchestration FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        assert row[0] == "split"
        # Marker bumped.
        assert _schema_version(conn) >= 10
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)  # second call must not error
    init_db(db)  # third either
    conn = sqlite3.connect(str(db))
    try:
        cols = _table_columns(conn, "runs")
        # No accidental duplicate column inserted (would raise via PRAGMA).
        assert sum(1 for n in cols if n == "orchestration") == 1
    finally:
        conn.close()


def test_orchestration_value_round_trips(tmp_path):
    """Inserting `orchestration='monolith'` reads back as 'monolith'."""
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, orchestration) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-28T00:00:00Z", "x.pdf", "completed", "monolith"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT orchestration FROM runs WHERE pdf_filename = 'x.pdf'"
        ).fetchone()
        assert row[0] == "monolith"
    finally:
        conn.close()
