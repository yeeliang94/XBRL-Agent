"""DB migration v15 -> v16: gold-standard eval / benchmark library.

v16 adds four additive tables (``eval_benchmarks``,
``eval_benchmark_templates``, ``gold_concept_facts``, ``eval_scores``) plus a
nullable ``runs.benchmark_id`` column so a run knows which benchmark to grade
against. See docs/PLAN-eval-benchmark.md.

Same pinning shape as ``test_db_schema_v13.py``: fresh init creates the
tables + column, a v15 fixture upgrades cleanly, re-init is idempotent, and the
FK cascades behave (delete a benchmark sweeps its templates / gold facts /
scores; delete a run nulls nothing on the run side but sweeps its scores).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


_EVAL_TABLES = (
    "eval_benchmarks",
    "eval_benchmark_templates",
    "gold_concept_facts",
    "eval_scores",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _seed_run(conn: sqlite3.Connection, benchmark_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, benchmark_id) "
        "VALUES ('2026-06-04T00:00:00Z', 'x.pdf', 'completed', ?)",
        (benchmark_id,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _seed_benchmark(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, document, filing_standard, "
        "filing_level, created_at) VALUES "
        "('FINCO 2021 MFRS Company', 'FINCO.pdf', 'mfrs', 'company', "
        "'2026-06-04T00:00:00Z')"
    )
    conn.commit()
    return int(cur.lastrowid)


def test_current_schema_version_is_at_least_v16():
    assert CURRENT_SCHEMA_VERSION >= 16


def test_fresh_init_creates_eval_tables_and_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for table in _EVAL_TABLES:
            assert _table_exists(conn, table), f"missing table {table!r}"

        # runs.benchmark_id added, nullable.
        run_cols = _table_columns(conn, "runs")
        assert "benchmark_id" in run_cols

        # Spot-check the gold-fact columns mirror run_concept_facts shape.
        gold_cols = _table_columns(conn, "gold_concept_facts")
        for required in (
            "benchmark_id", "concept_uuid", "period", "entity_scope",
            "value", "value_status", "source", "updated_at",
        ):
            assert required in gold_cols, f"gold missing {required!r}"

        # eval_scores carries the aggregate counts.
        score_cols = _table_columns(conn, "eval_scores")
        for required in (
            "run_id", "benchmark_id", "gold_cells", "matched_cells",
            "missing_cells", "mismatch_cells", "extra_cells", "scale_mismatch",
        ):
            assert required in score_cols, f"score missing {required!r}"

        assert _schema_version(conn) >= 16
    finally:
        conn.close()


def test_benchmark_delete_cascades_to_templates_gold_and_scores(tmp_path):
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        bench_id = _seed_benchmark(conn)
        # A concept_node is required for the gold FK; seed a template + node.
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES ('t1', '/tmp/t.xlsx', 'linear')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES ('c1', 't1', 'LEAF', 'Cash', 'SOFP', 5, 'B')"
        )
        conn.execute(
            "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
            "statement_type) VALUES (?, 't1', 'SOFP')",
            (bench_id,),
        )
        conn.execute(
            "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, "
            "period, entity_scope, value, value_status) "
            "VALUES (?, 'c1', 'CY', 'Company', 100.0, 'observed')",
            (bench_id,),
        )
        run_id = _seed_run(conn, benchmark_id=bench_id)
        conn.execute(
            "INSERT INTO eval_scores(run_id, benchmark_id, gold_cells, "
            "matched_cells, missing_cells, mismatch_cells, extra_cells, "
            "scale_mismatch) VALUES (?, ?, 1, 1, 0, 0, 0, 0)",
            (run_id, bench_id),
        )
        conn.commit()

        conn.execute("DELETE FROM eval_benchmarks WHERE id = ?", (bench_id,))
        conn.commit()

        for table in ("eval_benchmark_templates", "gold_concept_facts",
                      "eval_scores"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE benchmark_id = ?",
                (bench_id,),
            ).fetchone()[0]
            assert n == 0, f"{table} not cascaded"

        # The run row survives the benchmark delete; benchmark_id set NULL.
        row = conn.execute(
            "SELECT benchmark_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row is not None and row[0] is None
    finally:
        conn.close()


def test_gold_fact_unique_upsert_key(tmp_path):
    db = tmp_path / "uniq.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        bench_id = _seed_benchmark(conn)
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) "
            "VALUES ('t1', '/tmp/t.xlsx')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES ('c1', 't1', 'LEAF', 'Cash', 'SOFP', 5, 'B')"
        )
        conn.execute(
            "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, "
            "period, entity_scope, value, value_status) "
            "VALUES (?, 'c1', 'CY', 'Company', 100.0, 'observed')",
            (bench_id,),
        )
        conn.commit()
        # Same (benchmark, concept, period, scope) violates UNIQUE.
        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, "
                "period, entity_scope, value, value_status) "
                "VALUES (?, 'c1', 'CY', 'Company', 200.0, 'observed')",
                (bench_id,),
            )
    finally:
        conn.close()


def test_v15_fixture_upgrades_cleanly(tmp_path):
    """A v15 DB walks forward to v16: the eval tables appear, the column is
    added, the marker bumps to 16, and existing data is undisturbed."""
    db = tmp_path / "v15.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE runs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, pdf_filename TEXT NOT NULL, "
            "status TEXT NOT NULL, orchestration TEXT DEFAULT 'split')"
        )
        conn.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY)"
        )
        conn.execute("INSERT INTO schema_version(version) VALUES (15)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-01T00:00:00Z', 'legacy.pdf', 'completed')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        for table in _EVAL_TABLES:
            assert _table_exists(conn, table)
        assert "benchmark_id" in _table_columns(conn, "runs")
        assert _schema_version(conn) >= 16
        row = conn.execute(
            "SELECT pdf_filename FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        assert row[0] == "legacy.pdf"
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for table in _EVAL_TABLES:
            assert _table_exists(conn, table)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()
