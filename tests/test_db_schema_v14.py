"""DB migration v13 -> v14: cross_checks.comparands_json.

v14 adds the additive nullable ``cross_checks.comparands_json`` column
(reviewer holistic audit, Phase 2). It stores the values a cross-check
compared (both sides of a cross-statement equality, or the leaves of a
balance) so the reviewer gets concrete entry points instead of a bare diff.

Same pinning shape as the earlier additive-column steps
(``test_db_schema_v8.py`` etc.): fresh init carries the column, a v13
fixture upgrades cleanly, re-init is idempotent, and save/fetch round-trip
the JSON.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo
from cross_checks.framework import (
    Comparand, comparands_to_json, comparands_from_json,
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _seed_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-05-31T00:00:00Z', 'x.pdf', 'completed')"
    )
    conn.commit()
    return int(cur.lastrowid)


def test_current_schema_version_is_at_least_v14():
    assert CURRENT_SCHEMA_VERSION >= 14


def test_fresh_init_carries_comparands_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "comparands_json" in _columns(conn, "cross_checks")
        assert _schema_version(conn) >= 14
    finally:
        conn.close()


def test_v13_fixture_upgrades_to_v14(tmp_path):
    """A DB stamped at v13 without the column walks forward and gains it."""
    db = tmp_path / "old.db"
    init_db(db)  # build current schema...
    conn = sqlite3.connect(str(db))
    try:
        # ...then simulate a v13 DB: drop the column is impossible in SQLite,
        # so instead rebuild cross_checks without it and stamp version=13.
        conn.execute("DROP TABLE cross_checks")
        conn.execute(
            "CREATE TABLE cross_checks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id INTEGER NOT NULL, check_name TEXT NOT NULL, status TEXT "
            "NOT NULL, expected REAL, actual REAL, diff REAL, tolerance REAL, "
            "message TEXT, target_sheet TEXT, target_row INTEGER)"
        )
        conn.execute("UPDATE schema_version SET version = 13")
        conn.commit()
        assert "comparands_json" not in _columns(conn, "cross_checks")
    finally:
        conn.close()
    # Re-init must migrate v13 -> v14 by ALTER-ing the column in.
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "comparands_json" in _columns(conn, "cross_checks")
        assert _schema_version(conn) >= 14
    finally:
        conn.close()


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "x.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "comparands_json" in _columns(conn, "cross_checks")
    finally:
        conn.close()


def test_save_and_fetch_round_trip_comparands(tmp_path):
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        run_id = _seed_run(conn)
        comparands = [
            Comparand(label="Profit (loss)", sheet="SOPL-Function",
                      value=-20633.0, role="lhs", statement="SOPL"),
            Comparand(label="Profit (loss)", sheet="SOCIE", value=-20678.0,
                      role="rhs", statement="SOCIE"),
        ]
        repo.save_cross_check(
            conn, run_id, check_name="sopl_to_socie_profit", status="failed",
            expected=-20633.0, actual=-20678.0, diff=45.0,
            comparands_json=comparands_to_json(comparands),
        )
        conn.commit()
        fetched = repo.fetch_cross_checks(conn, run_id)
        assert len(fetched) == 1
        decoded = comparands_from_json(fetched[0].comparands_json)
        assert [d.role for d in decoded] == ["lhs", "rhs"]
        assert decoded[0].value == -20633.0
        assert decoded[1].sheet == "SOCIE"
    finally:
        conn.close()


def test_comparands_json_helpers_are_tolerant():
    assert comparands_to_json([]) is None
    assert comparands_to_json(None) is None
    assert comparands_from_json(None) == []
    assert comparands_from_json("not json{") == []
    assert comparands_from_json('{"not": "a list"}') == []
    # Unknown keys are dropped, not fatal (forward-compat).
    assert comparands_from_json('[{"label":"x","sheet":"S","bogus":1}]')[0].label == "x"
