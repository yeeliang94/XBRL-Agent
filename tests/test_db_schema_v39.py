"""Schema v39 — the PROSE half of an mTool fill's data snapshot.

v38 recorded which revision of `run_concept_facts` a filled workbook came
from. It did not record the prose. The notes are read from `notes_cells` by a
separate connection later in the same request
(`mtool.notes_exporter.build_notes_fill_doc`), so a notes edit landing between
the two reads produced a workbook whose prose belonged to a revision no
receipt described — the exact condition the receipts table exists to prevent.

Three nullable columns, because a numeric-only fill legitimately has no prose
revision and a pre-v39 receipt asserts nothing about one.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_NOTES_SNAPSHOT_COLUMNS = {
    "snapshot_notes_count",
    "snapshot_notes_digest",
    "snapshot_notes_updated",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_carries_the_notes_snapshot_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _NOTES_SNAPSHOT_COLUMNS <= _columns(conn, "mtool_fill_receipts")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 39
    finally:
        conn.close()


def test_v38_db_walks_forward(tmp_path):
    """An existing v38 database gains the columns without losing its rows."""
    db = tmp_path / "v38.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')").lastrowid
        # Rebuild the table as it stood at v38, with a receipt already in it.
        conn.execute("DROP TABLE mtool_fill_receipts")
        conn.execute(
            """
            CREATE TABLE mtool_fill_receipts (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                INTEGER NOT NULL
                                      REFERENCES runs(id) ON DELETE CASCADE,
                snapshot_fact_count   INTEGER,
                snapshot_digest       TEXT,
                snapshot_max_updated  TEXT,
                source_sha256         TEXT,
                output_sha256         TEXT,
                template_fingerprint  TEXT,
                column_map_json       TEXT,
                translation_version   TEXT,
                preflight_json        TEXT,
                preflight_override    TEXT,
                degraded_ack          TEXT,
                status                TEXT,
                report_json           TEXT,
                operator              TEXT,
                created_at            TEXT NOT NULL DEFAULT '',
                downloaded_at         TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mtool_fill_receipts(run_id, snapshot_digest, "
            "status, created_at) VALUES (?, 'numeric-digest', 'ok', 't')",
            (run_id,))
        conn.execute("UPDATE schema_version SET version = 38")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _NOTES_SNAPSHOT_COLUMNS <= _columns(conn, "mtool_fill_receipts")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        # The pre-existing receipt survives, and asserts nothing about prose.
        row = conn.execute(
            "SELECT snapshot_digest, snapshot_notes_digest "
            "FROM mtool_fill_receipts").fetchone()
        assert row[0] == "numeric-digest"
        assert row[1] is None
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _NOTES_SNAPSHOT_COLUMNS <= _columns(conn, "mtool_fill_receipts")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_notes_snapshot_columns_are_nullable(tmp_path):
    """A numeric-only fill has no prose revision — that must insert cleanly."""
    db = tmp_path / "n.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')").lastrowid
        conn.execute(
            "INSERT INTO mtool_fill_receipts(run_id, created_at) "
            "VALUES (?, 't')", (run_id,))
        conn.commit()
        assert conn.execute(
            "SELECT snapshot_notes_count FROM mtool_fill_receipts"
        ).fetchone()[0] is None
    finally:
        conn.close()
