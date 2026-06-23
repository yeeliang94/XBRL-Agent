"""DB migration v24 -> v25: the notes-reviewer tombstone table.

Adds `notes_cell_tombstones` — the durable record of coordinates the reviewer
emptied (clear / move-out / authored-then-reverted) so the workbook overlay can
blank them. New table (pure CREATE TABLE IF NOT EXISTS walk-forward, no ALTER).
Pinning mirrors the other table-only steps: fresh init carries the table +
version 25, a v24 fixture walks forward, and re-init is idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_NEW_TABLES = ("notes_cell_tombstones",)


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


def test_current_schema_version_is_at_least_v25():
    assert CURRENT_SCHEMA_VERSION >= 25


def test_fresh_init_has_tombstone_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        for t in _NEW_TABLES:
            assert t in tables, f"missing {t}"
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        # The per-run index exists.
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "ix_notes_cell_tombstones_run_id" in idx
    finally:
        conn.close()


def test_v24_db_walks_forward(tmp_path):
    db = tmp_path / "v24.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Simulate a committed v24 install: drop the new table, reset the marker.
        for t in _NEW_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("UPDATE schema_version SET version = 24")
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


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_cell_tombstones" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_tombstone_unique_key(tmp_path):
    """One tombstone per (run_id, sheet, row) — INSERT OR CONFLICT helper."""
    from db import repository as repo

    db = tmp_path / "uniq.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        repo.add_notes_tombstone(conn, run_id=run_id, sheet="S", row=5)
        repo.add_notes_tombstone(conn, run_id=run_id, sheet="S", row=5)  # idempotent
        assert repo.fetch_notes_tombstones(conn, run_id) == [("S", 5)]
        repo.remove_notes_tombstone(conn, run_id=run_id, sheet="S", row=5)
        assert repo.fetch_notes_tombstones(conn, run_id) == []
