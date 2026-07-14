"""Schema v33 — gold-change guard + benchmark archive (PLAN-evals-hardening
Steps 7-9/14).

Adds eval_scores.gold_fingerprint (stamped at grade time; NULL on legacy
rows) and eval_benchmarks.is_archived / source / scale_verified. All additive.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_v33_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "gold_fingerprint" in _columns(conn, "eval_scores")
        assert {"is_archived", "source", "scale_verified"} <= _columns(
            conn, "eval_benchmarks"
        )
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 33
    finally:
        conn.close()


def test_v32_db_walks_forward(tmp_path):
    db = tmp_path / "v32.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for col in ("is_archived", "source", "scale_verified"):
            conn.execute(f"ALTER TABLE eval_benchmarks DROP COLUMN {col}")
        conn.execute("ALTER TABLE eval_scores DROP COLUMN gold_fingerprint")
        conn.execute("UPDATE schema_version SET version = 32")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "gold_fingerprint" in _columns(conn, "eval_scores")
        assert {"is_archived", "source", "scale_verified"} <= _columns(
            conn, "eval_benchmarks"
        )
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_legacy_benchmarks_default_unarchived_verified(tmp_path):
    db = tmp_path / "defaults.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
            "VALUES ('B', 'mfrs', 'company')"
        )
        row = conn.execute(
            "SELECT is_archived, source, scale_verified FROM eval_benchmarks"
        ).fetchone()
        assert row == (0, None, 1)
    finally:
        conn.close()
