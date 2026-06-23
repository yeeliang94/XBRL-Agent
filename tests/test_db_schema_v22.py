"""DB migration v21 -> v22: the runs.notes_table_style column.

Per-run notes-table style override (docs/PLAN-notes-table-theme.md). One
additive nullable ALTER. Pinning shape mirrors v20: fresh init carries the
column + version 22, a v21 fixture walks forward, and re-init is idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v22():
    assert CURRENT_SCHEMA_VERSION >= 22


def test_fresh_init_has_notes_table_style_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_table_style" in _columns(conn, "runs")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v21_db_walks_forward(tmp_path):
    db = tmp_path / "v21.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Simulate a committed v21 install: drop the new column, reset the marker.
        conn.executescript(
            """
            ALTER TABLE runs DROP COLUMN notes_table_style;
            UPDATE schema_version SET version = 21;
            """
        )
        conn.commit()
        assert "notes_table_style" not in _columns(conn, "runs")
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_table_style" in _columns(conn, "runs")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_notes_table_style_defaults_null(tmp_path):
    """A run inserted without the override reads NULL (inherits firm default)."""
    db = tmp_path / "null.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs (id, status, created_at, pdf_filename) "
            "VALUES (1, 'draft', '2026-06-23T00:00:00', 'x.pdf')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT notes_table_style FROM runs WHERE id = 1"
        ).fetchone()
        assert row[0] is None
    finally:
        conn.close()


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()
