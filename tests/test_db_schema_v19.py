"""DB migration v18 -> v19: the notes_nodes table (prose notes registry).

Track A of PLAN-notes-template-registry. notes_nodes is a brand-new table, so the
step is a pure CREATE TABLE IF NOT EXISTS walk-forward (no ALTER) — same pinning
shape as the v12->v13 / v17->v18 table-only steps: fresh init carries the table +
version 19, a v18 fixture walks forward cleanly, re-init is idempotent, and the
table's key constraints (template-scoped PK + UNIQUE(template_id, sheet, row))
behave as designed.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v19():
    # >= so a later bump doesn't break this pin (resilient convention).
    assert CURRENT_SCHEMA_VERSION >= 19


def test_fresh_init_has_notes_nodes_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_nodes" in _tables(conn)
        # Exact column set — guards against a silent column rename/drop.
        assert _columns(conn, "notes_nodes") == {
            "node_uuid",
            "template_id",
            "sheet",
            "row",
            "label",
            "kind",
            "xbrl_concept_id",
        }
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v18_db_walks_forward_to_v19(tmp_path):
    db = tmp_path / "v18.db"
    # Build a fresh DB, then simulate a committed v18 install by dropping the
    # new table and resetting the version marker.
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE notes_nodes")
        conn.execute("UPDATE schema_version SET version = 18")
        conn.commit()
        assert "notes_nodes" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_nodes" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
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


def test_notes_nodes_key_constraints(tmp_path):
    """node_uuid PK is unique; (template_id, sheet, row) is the upsert key."""
    db = tmp_path / "keys.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("uuid-a", "mfrs-company-notes-corporateinfo-v1", "Notes-CI", 4, "Corporate info", "LEAF"),
        )
        conn.commit()

        # Same (template_id, sheet, row) with a different uuid → UNIQUE violation.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("uuid-b", "mfrs-company-notes-corporateinfo-v1", "Notes-CI", 4, "Dup coord", "LEAF"),
            )
        conn.rollback()

        # The SAME (sheet, row, label) under a DIFFERENT template_id is allowed —
        # this is the cross-family collision the template-scoped key prevents.
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("uuid-c", "mfrs-group-notes-corporateinfo-v1", "Notes-CI", 4, "Corporate info", "LEAF"),
        )
        conn.commit()

        assert conn.execute("SELECT COUNT(*) FROM notes_nodes").fetchone()[0] == 2
        # xbrl_concept_id defaults to NULL (reserved column).
        nulls = conn.execute(
            "SELECT COUNT(*) FROM notes_nodes WHERE xbrl_concept_id IS NULL"
        ).fetchone()[0]
        assert nulls == 2
    finally:
        conn.close()
