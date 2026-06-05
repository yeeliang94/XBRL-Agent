"""Seed a benchmark directly from a run's facts (eval/store.create_benchmark_from_run).

The lossless counterpart to the workbook-upload path: copying
``run_concept_facts`` straight into ``gold_concept_facts`` captures every
sub-sheet and matrix LEAF the run wrote, with NO openpyxl formula-cache loss
(the 2026-06-05 sub-sheet-loss incident, gotcha #23). These tests pin:

* sub-sheet + matrix leaves are captured, COMPUTED totals are excluded;
* the template SET is derived from the facts (variant-precise);
* non-finished runs (draft / running / failed) are rejected;
* a run with no gradeable facts is rejected.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.schema import init_db
from statement_types import StatementType, template_path


def _import_company_sofp(db_path) -> str:
    """Import the live MFRS Company SOFP-CuNonCu template; return template_id."""
    from concept_model.importer import import_template, import_company_targets
    from concept_model.parser import parse_template

    tpath = template_path(StatementType.SOFP, "CuNonCu", "company", "mfrs")
    tree = parse_template(str(tpath))
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(tree.to_json(), fh, sort_keys=True)
        json_path = fh.name
    try:
        template_id = import_template(db_path, json_path)
    finally:
        Path(json_path).unlink(missing_ok=True)
    import_company_targets(db_path, template_id)
    return template_id


def _make_run(conn, status: str, *, standard="mfrs", level="company") -> int:
    cfg = {"filing_standard": standard, "filing_level": level}
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
        "run_config_json) VALUES (?, ?, ?, ?, ?)",
        ("2026-06-05T00:00:00Z", "x.pdf", status, "2026-06-05T00:00:00Z",
         json.dumps(cfg)),
    )
    return int(cur.lastrowid)


def _seed_run_fact(conn, run_id, concept_uuid, value, *,
                   period="CY", value_status="observed") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_concept_facts("
        "run_id, concept_uuid, period, entity_scope, value, value_status, "
        "children_status, source, updated_at) "
        "VALUES (?, ?, ?, 'Company', ?, ?, NULL, 'pdf p.1', '2026-06-05Z')",
        (run_id, concept_uuid, period, value, value_status),
    )


def _leaf_and_computed(conn, template_id):
    """Return (list of LEAF uuids ordered by sheet/row, one COMPUTED uuid)."""
    leaves = [
        r[0] for r in conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'LEAF' "
            "ORDER BY render_sheet, render_row",
            (template_id,),
        ).fetchall()
    ]
    computed = conn.execute(
        "SELECT concept_uuid FROM concept_nodes "
        "WHERE template_id = ? AND kind = 'COMPUTED' LIMIT 1",
        (template_id,),
    ).fetchone()
    return leaves, (computed[0] if computed else None)


def test_from_run_captures_leaves_and_excludes_computed(tmp_path):
    db = tmp_path / "fromrun.db"
    init_db(db)
    template_id = _import_company_sofp(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        from eval import store

        run_id = _make_run(conn, "completed_with_errors")
        leaves, computed = _leaf_and_computed(conn, template_id)
        assert len(leaves) >= 3 and computed is not None

        # Three CY leaves + one PY leaf + a COMPUTED total (must be skipped).
        _seed_run_fact(conn, run_id, leaves[0], 100.0)
        _seed_run_fact(conn, run_id, leaves[1], 200.0)
        _seed_run_fact(conn, run_id, leaves[2], 300.0)
        _seed_run_fact(conn, run_id, leaves[0], 90.0, period="PY")
        _seed_run_fact(conn, run_id, computed, 600.0)  # COMPUTED — excluded
        conn.commit()

        result = store.create_benchmark_from_run(
            conn, name="From run 1", run_id=run_id
        )
        conn.commit()

        # 4 gradeable facts (3 CY leaves + 1 PY leaf); COMPUTED dropped.
        assert result["ingested"] == 4
        assert result["source_run_id"] == run_id
        assert result["source_run_status"] == "completed_with_errors"

        bench_id = result["id"]
        gold = conn.execute(
            "SELECT COUNT(*) FROM gold_concept_facts WHERE benchmark_id = ?",
            (bench_id,),
        ).fetchone()[0]
        assert gold == 4
        # The COMPUTED total never became gold.
        assert conn.execute(
            "SELECT COUNT(*) FROM gold_concept_facts "
            "WHERE benchmark_id = ? AND concept_uuid = ?",
            (bench_id, computed),
        ).fetchone()[0] == 0
        # The template set was derived from the facts.
        tset = [
            r[0] for r in conn.execute(
                "SELECT template_id FROM eval_benchmark_templates "
                "WHERE benchmark_id = ?", (bench_id,),
            ).fetchall()
        ]
        assert tset == [template_id]
        # A copied leaf keeps its value + period.
        py = conn.execute(
            "SELECT value FROM gold_concept_facts WHERE benchmark_id = ? "
            "AND concept_uuid = ? AND period = 'PY'",
            (bench_id, leaves[0]),
        ).fetchone()
        assert py is not None and abs(py[0] - 90.0) < 1e-9
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["draft", "running", "failed", "aborted"])
def test_from_run_rejects_unfinished_runs(tmp_path, status):
    db = tmp_path / f"reject_{status}.db"
    init_db(db)
    template_id = _import_company_sofp(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        from eval import store

        run_id = _make_run(conn, status)
        leaves, _ = _leaf_and_computed(conn, template_id)
        _seed_run_fact(conn, run_id, leaves[0], 100.0)
        conn.commit()

        with pytest.raises(ValueError, match="finished run"):
            store.create_benchmark_from_run(conn, name="bad", run_id=run_id)
    finally:
        conn.close()


def test_from_run_rejects_run_with_no_gradeable_facts(tmp_path):
    db = tmp_path / "empty.db"
    init_db(db)
    _import_company_sofp(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        from eval import store

        run_id = _make_run(conn, "completed")  # no facts seeded
        conn.commit()
        with pytest.raises(ValueError, match="no gradeable extracted facts"):
            store.create_benchmark_from_run(conn, name="empty", run_id=run_id)
    finally:
        conn.close()


def test_from_run_rejects_all_not_disclosed_gold(tmp_path):
    """A run whose only gradeable facts are not_disclosed/blank copies rows but
    would grade 0/0 — reject it like the workbook path does (peer-review HIGH)."""
    db = tmp_path / "nd.db"
    init_db(db)
    template_id = _import_company_sofp(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        from eval import store

        run_id = _make_run(conn, "completed")
        leaves, _ = _leaf_and_computed(conn, template_id)
        # Two gradeable leaves, both with no usable value: one not_disclosed,
        # one blank (NULL value, observed) → denominator is zero.
        _seed_run_fact(conn, run_id, leaves[0], None, value_status="not_disclosed")
        _seed_run_fact(conn, run_id, leaves[1], None, value_status="observed")
        conn.commit()

        with pytest.raises(ValueError, match="blank or not-disclosed"):
            store.create_benchmark_from_run(conn, name="nd", run_id=run_id)
    finally:
        conn.close()


def test_from_run_unknown_run_raises(tmp_path):
    db = tmp_path / "missing.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        from eval import store

        with pytest.raises(ValueError, match="not found"):
            store.create_benchmark_from_run(conn, name="x", run_id=99999)
    finally:
        conn.close()
