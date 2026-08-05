"""Schema v38 — mtool_fill_receipts (durable record of every mTool fill).

New table only, no ALTER. A filled MBRS workbook is a regulatory artifact, and
a completed run stays editable, so "which revision of the data produced this
file" has to be recorded at fill time or it is unrecoverable. Originally
minted as v35 by the reverted v2 build (b04b178); re-minted as v38 because
v35–v37 were taken by the notes source-integrity model in the meantime
(plan Step 19, peer-review finding 6).
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_mtool_fill_receipts(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert {
            "run_id", "snapshot_fact_count", "snapshot_digest",
            "snapshot_max_updated", "source_sha256", "output_sha256",
            "template_fingerprint", "column_map_json", "translation_version",
            "preflight_json", "preflight_override", "degraded_ack", "status",
            "report_json", "operator", "created_at", "downloaded_at",
        } <= _columns(conn, "mtool_fill_receipts")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 38
    finally:
        conn.close()


def test_v37_db_walks_forward(tmp_path):
    db = tmp_path / "v37.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE mtool_fill_receipts")
        conn.execute("UPDATE schema_version SET version = 37")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "output_sha256" in _columns(conn, "mtool_fill_receipts")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_status_has_no_check_constraint(tmp_path):
    """Gotcha #11: a new status value must never require a migration."""
    db = tmp_path / "s.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'mtool_fill_receipts'"
        ).fetchone()[0]
        # Strip -- comments first; one of them mentions the absent constraint.
        code = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
        assert "CHECK" not in code.upper()
        # And prove it behaves: an unforeseen status inserts fine.
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')").lastrowid
        conn.execute(
            "INSERT INTO mtool_fill_receipts(run_id, status, created_at) "
            "VALUES (?, ?, ?)", (run_id, "a-status-invented-later", "t"))
        conn.commit()
    finally:
        conn.close()


def test_receipts_cascade_with_the_run(tmp_path):
    db = tmp_path / "c.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')").lastrowid
        conn.execute(
            "INSERT INTO mtool_fill_receipts(run_id, created_at) VALUES (?, ?)",
            (run_id, "t"))
        conn.commit()
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM mtool_fill_receipts").fetchone()[0] == 0
    finally:
        conn.close()
