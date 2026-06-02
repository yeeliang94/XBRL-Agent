"""DB migration v14 -> v15: cache telemetry columns.

v15 is part of the §6 caching work (see docs/REVIEW-prompts-and-caching.html
and docs/PLAN.md): "measure before you optimize." It adds nullable
``cache_read_tokens`` / ``cache_write_tokens`` columns to ``run_agents``
(run-level rollup) and ``run_agent_turns`` (per-turn delta). ``cache_read``
is the only proof that prompt caching is actually hitting; ``cache_write`` is
tracked separately because Anthropic prices cache writes at a premium and a
reads-only metric would report phantom savings.

Same pinning shape as ``test_db_schema_v8.py`` (the other ALTER-columns step
on these tables): fresh init carries the columns, a v14 fixture upgrades
cleanly, re-init is idempotent, and the repository round-trips the values.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


_CACHE_COLS = ("cache_read_tokens", "cache_write_tokens")


def test_current_schema_version_is_at_least_v15():
    assert CURRENT_SCHEMA_VERSION >= 15


def test_fresh_init_carries_cache_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for table in ("run_agents", "run_agent_turns"):
            cols = _table_columns(conn, table)
            for required in _CACHE_COLS:
                assert required in cols, f"{table} missing {required!r}"
        assert _schema_version(conn) >= 15
    finally:
        conn.close()


def test_cache_rollup_round_trips(tmp_path):
    """finish_run_agent persists the cache rollup and fetch_run_agents reads
    it back; insert_agent_turns / fetch_agent_turns round-trip the per-turn
    deltas."""
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-06-02T00:00:00Z', 'x.pdf', 'completed')"
        )
        run_id = int(cur.lastrowid)
        run_agent_id = repo.create_run_agent(
            conn, run_id, statement_type="SOFP", variant=None, model="m1"
        )

        repo.insert_agent_turns(conn, run_agent_id, [
            {"turn_index": 1, "node_kind": "model_request",
             "prompt_tokens": 1000, "completion_tokens": 50,
             "cache_read_tokens": 0, "cache_write_tokens": 900},
            {"turn_index": 2, "node_kind": "model_request",
             "prompt_tokens": 1000, "completion_tokens": 40,
             "cache_read_tokens": 900, "cache_write_tokens": 0},
        ])
        repo.finish_run_agent(
            conn, run_agent_id, status="succeeded",
            cache_read_tokens=900, cache_write_tokens=900,
        )
        conn.commit()

        agents = repo.fetch_run_agents(conn, run_id)
        assert len(agents) == 1
        assert agents[0].cache_read_tokens == 900
        assert agents[0].cache_write_tokens == 900

        turns = repo.fetch_agent_turns(conn, run_agent_id)
        assert [t["cache_read_tokens"] for t in turns] == [0, 900]
        assert [t["cache_write_tokens"] for t in turns] == [900, 0]
    finally:
        conn.close()


def test_v14_fixture_upgrades_cleanly(tmp_path):
    """A v14 DB whose run_agents / run_agent_turns predate the cache columns
    walks forward to v15: the columns appear (default 0), the marker bumps,
    and existing rows are undisturbed."""
    db = tmp_path / "v14.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE runs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, pdf_filename TEXT NOT NULL, "
            "status TEXT NOT NULL)"
        )
        # run_agents / run_agent_turns as they existed at v14: with the v8
        # rollup/turn columns but WITHOUT the v15 cache columns, so the ALTER
        # genuinely runs.
        conn.execute(
            "CREATE TABLE run_agents("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, "
            "statement_type TEXT NOT NULL, variant TEXT, model TEXT, "
            "status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, "
            "workbook_path TEXT, total_tokens INTEGER DEFAULT 0, "
            "total_cost REAL DEFAULT 0, prompt_tokens INTEGER DEFAULT 0, "
            "completion_tokens INTEGER DEFAULT 0, turn_count INTEGER DEFAULT 0, "
            "tool_call_count INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE run_agent_turns("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_agent_id INTEGER NOT NULL, "
            "turn_index INTEGER NOT NULL, node_kind TEXT, tool_names TEXT, "
            "prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, "
            "total_tokens INTEGER DEFAULT 0, cumulative_tokens INTEGER DEFAULT 0, "
            "cost_estimate REAL DEFAULT 0, duration_ms INTEGER DEFAULT 0, "
            "ts TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (14)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-01T00:00:00Z', 'legacy.pdf', 'completed')"
        )
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, status, started_at) "
            "VALUES (1, 'SOFP', 'succeeded', '2026-05-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        for table in ("run_agents", "run_agent_turns"):
            cols = _table_columns(conn, table)
            for required in _CACHE_COLS:
                assert required in cols, f"{table} missing {required!r}"
        assert _schema_version(conn) >= 15
        # Legacy row preserved; cache column defaults to 0.
        row = conn.execute(
            "SELECT cache_read_tokens FROM run_agents "
            "WHERE statement_type = 'SOFP'"
        ).fetchone()
        assert row[0] == 0
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        cols = _table_columns(conn, "run_agents")
        for required in _CACHE_COLS:
            assert required in cols
    finally:
        conn.close()
