"""Schema v32 — suite-run corpus snapshot (PLAN-evals-hardening Step 2).

Adds one table (eval_suite_run_docs — the frozen per-suite-run document list
with per-doc execution state) and one column (eval_suite_docs.denomination).
All additive; a v31 DB walks forward gaining them.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


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


def test_fresh_init_has_snapshot_table_and_denomination(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "eval_suite_run_docs" in _tables(conn)
        cols = _columns(conn, "eval_suite_run_docs")
        assert {
            "suite_run_id", "suite_doc_id", "label", "source_path",
            "source_filename", "source_sha256", "filing_standard",
            "filing_level", "benchmark_id", "denomination", "variants_json",
            "state", "error",
        } <= cols
        assert "denomination" in _columns(conn, "eval_suite_docs")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 32
    finally:
        conn.close()


def test_v31_db_walks_forward(tmp_path):
    """A DB pinned at v31 gains the snapshot table + denomination on init_db."""
    db = tmp_path / "v31.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE eval_suite_run_docs")
        # Rebuild eval_suite_docs without the denomination column (v31 shape).
        conn.execute("ALTER TABLE eval_suite_docs DROP COLUMN denomination")
        conn.execute("UPDATE schema_version SET version = 31")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "eval_suite_run_docs" in _tables(conn)
        assert "denomination" in _columns(conn, "eval_suite_docs")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_snapshot_rows_survive_doc_deletion(tmp_path):
    """The snapshot is a frozen copy: deleting the live suite doc must NOT
    cascade into eval_suite_run_docs (no FK on suite_doc_id on purpose)."""
    db = tmp_path / "frozen.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO eval_suites(name) VALUES ('S')")
        suite_id = conn.execute("SELECT id FROM eval_suites").fetchone()[0]
        conn.execute(
            "INSERT INTO eval_suite_docs(suite_id, label, source_path) "
            "VALUES (?, 'd', '/tmp/d.pdf')",
            (suite_id,),
        )
        doc_id = conn.execute("SELECT id FROM eval_suite_docs").fetchone()[0]
        conn.execute(
            "INSERT INTO eval_suite_runs(suite_id) VALUES (?)", (suite_id,)
        )
        sr_id = conn.execute("SELECT id FROM eval_suite_runs").fetchone()[0]
        conn.execute(
            "INSERT INTO eval_suite_run_docs(suite_run_id, suite_doc_id, label) "
            "VALUES (?, ?, 'd')",
            (sr_id, doc_id),
        )
        conn.execute("DELETE FROM eval_suite_docs WHERE id = ?", (doc_id,))
        conn.commit()
        row = conn.execute(
            "SELECT suite_doc_id FROM eval_suite_run_docs WHERE suite_run_id = ?",
            (sr_id,),
        ).fetchone()
        assert row is not None and row[0] == doc_id
    finally:
        conn.close()
