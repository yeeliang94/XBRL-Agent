"""DB migration v22 -> v23: the three notes-reviewer tables.

Adds `notes_cell_provenance`, `run_notes_inventory`, `run_notes_cell_snapshots`
(docs/PLAN.md — Notes Reviewer). All three are new tables (pure CREATE TABLE IF
NOT EXISTS walk-forward, no ALTER columns). Pinning mirrors the other
table-only steps (v18→v19): fresh init carries the tables + version 23, a v22
fixture walks forward, and re-init is idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_NEW_TABLES = (
    "notes_cell_provenance",
    "run_notes_inventory",
    "run_notes_cell_snapshots",
    "run_notes_review_state",
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v23():
    assert CURRENT_SCHEMA_VERSION >= 23


def test_fresh_init_has_notes_reviewer_tables(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        for t in _NEW_TABLES:
            assert t in tables, f"missing {t}"
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v22_db_walks_forward(tmp_path):
    db = tmp_path / "v22.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Simulate a committed v22 install: drop the new tables, reset the marker.
        for t in _NEW_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("UPDATE schema_version SET version = 22")
        conn.commit()
        assert not (_tables(conn) & set(_NEW_TABLES))
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        for t in _NEW_TABLES:
            assert t in tables
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_provenance_unique_key(tmp_path):
    """One provenance row per (run_id, sheet, row) — upsert key."""
    db = tmp_path / "uniq.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs (id, status, created_at, pdf_filename) "
            "VALUES (1, 'completed', '2026-06-23T00:00:00', 'x.pdf')"
        )
        conn.execute(
            "INSERT INTO notes_cell_provenance (run_id, sheet, row, row_label, "
            "source_note_refs) VALUES (1, 'Notes-Listofnotes', 49, 'X', '[\"4.1\"]')"
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO notes_cell_provenance (run_id, sheet, row, row_label) "
                "VALUES (1, 'Notes-Listofnotes', 49, 'Y')"
            )
            conn.commit()
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "expected UNIQUE(run_id, sheet, row) to reject the dup"
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
