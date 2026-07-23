"""Schema v34 — run_lineage (stage-level resume audit trail, plan
agent-efficiency Phase 4A).

New table only, no ALTER: a resume creates a child run linked to its
immutable terminal parent, recording the parent's source hash (computed at
resume time from the kept uploaded.pdf) and which statements were reused vs
rerun. Inert unless XBRL_STAGE_RESUME is used.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_run_lineage(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert {"child_run_id", "parent_run_id", "source_sha256",
                "reused_statements", "rerun_statements",
                "created_at"} <= _columns(conn, "run_lineage")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 34
    finally:
        conn.close()


def test_v33_db_walks_forward(tmp_path):
    db = tmp_path / "v33.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE run_lineage")
        conn.execute("UPDATE schema_version SET version = 33")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "child_run_id" in _columns(conn, "run_lineage")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_lineage_rows_cascade_with_child_run(tmp_path):
    """Deleting the CHILD run sweeps its lineage row; the parent link is a
    plain reference (parent stays immutable and deletable independently)."""
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        parent = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-07-23T00:00:00Z','x.pdf','failed')").lastrowid)
        child = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-07-23T01:00:00Z','x.pdf','running')").lastrowid)
        conn.execute(
            "INSERT INTO run_lineage(child_run_id, parent_run_id, "
            "source_sha256, reused_statements, rerun_statements, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (child, parent, "ab" * 32, '["SOFP/CuNonCu"]', '["SOPL/Default"]',
             "2026-07-23T01:00:00Z"))
        conn.commit()
        conn.execute("DELETE FROM runs WHERE id = ?", (child,))
        conn.commit()
        assert conn.execute(
            "SELECT count(*) FROM run_lineage").fetchone()[0] == 0
    finally:
        conn.close()
