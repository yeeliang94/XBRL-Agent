"""DB migration v20 -> v21: the doc_conversions table.

Durable conversion-job state for the (now removed) scanned-PDF → readable-document
feature (docs/PLAN-deprecate-docconvert.md). The table is RETAINED as an inert
artifact so the migration chain stays intact. New table → pure CREATE TABLE IF
NOT EXISTS walk-forward (no ALTER), same pinning shape as v18→v19: fresh init
carries the table + version 21, a v20 fixture walks forward, and re-init is
idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v21():
    assert CURRENT_SCHEMA_VERSION >= 21


def test_fresh_init_has_doc_conversions_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "doc_conversions" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(doc_conversions)").fetchall()
        }
        assert {"status", "source_pdf_path", "result_html_path", "error",
                "total_pages", "current_page"} <= cols
    finally:
        conn.close()


def test_v20_db_walks_forward(tmp_path):
    db = tmp_path / "v20.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Simulate a committed v20 install: drop the new table, reset the marker.
        conn.executescript(
            """
            DROP TABLE IF EXISTS doc_conversions;
            UPDATE schema_version SET version = 20;
            """
        )
        conn.commit()
        assert "doc_conversions" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        assert "doc_conversions" in _tables(conn)
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
