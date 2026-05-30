"""Phase B / peer-review finding 5 — the live extraction tool projection path.

The fill_workbook tool calls `_project_facts_if_canonical(deps, result)` and
surfaces its warning to the agent. These tests pin that helper directly (no
LLM): it projects facts when canonical deps are set, no-ops in legacy mode, and
returns an advisory string (not an exception) when cells don't map — so a
projection gap is visible rather than silent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from extraction.agent import ExtractionDeps, _project_facts_if_canonical
from statement_types import StatementType
from token_tracker import TokenReport
from tools.fill_workbook import fill_workbook


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def canonical_env(tmp_path: Path):
    from db.schema import init_db
    from concept_model.parser import parse_template, _derive_template_id
    from concept_model.importer import import_template

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    tree = parse_template(str(CO_SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db_path, jp)
    template_id = _derive_template_id(CO_SOFP)

    conn = sqlite3.connect(str(db_path))
    run_id = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?, ?, ?, ?)",
        ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
    ).lastrowid
    leaf_row = conn.execute(
        "SELECT render_row FROM concept_nodes WHERE template_id=? "
        "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 1",
        (template_id,),
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return db_path, run_id, template_id, int(leaf_row)


def _deps(tmp_path, *, run_id=None, db_path=None, template_id=None):
    return ExtractionDeps(
        pdf_path="x.pdf",
        template_path=str(CO_SOFP),
        model="test",
        output_dir=str(tmp_path),
        token_report=TokenReport(model="test"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        run_id=run_id,
        db_path=db_path,
        template_id=template_id,
    )


def _fill(tmp_path, row, col=2, value=123.0):
    out = tmp_path / "out.xlsx"
    facts = [
        {"sheet": "SOFP-CuNonCu", "row": row, "col": col, "value": value}
    ]
    return fill_workbook(str(CO_SOFP), str(out), facts, filing_level="company")


def test_projection_runs_when_canonical_deps_set(canonical_env, tmp_path):
    db_path, run_id, template_id, leaf = canonical_env
    deps = _deps(tmp_path, run_id=run_id, db_path=str(db_path), template_id=template_id)
    result = _fill(tmp_path, leaf, value=4321.0)

    warning = _project_facts_if_canonical(deps, result)
    assert warning is None  # clean projection, no gaps

    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY'",
            (run_id,),
        ).fetchone()[0]
        assert val == 4321.0
    finally:
        conn.close()


def test_projection_noops_in_legacy_mode(canonical_env, tmp_path):
    db_path, run_id, _tid, leaf = canonical_env
    # No run_id/db_path/template_id → legacy mode, no projection.
    deps = _deps(tmp_path)
    result = _fill(tmp_path, leaf)
    assert _project_facts_if_canonical(deps, result) is None

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_projection_warns_on_unmapped_cell(canonical_env, tmp_path):
    db_path, run_id, template_id, leaf = canonical_env
    deps = _deps(tmp_path, run_id=run_id, db_path=str(db_path), template_id=template_id)
    # col 4 (evidence column) doesn't map to a concept on a Company sheet.
    result = _fill(tmp_path, leaf, col=4, value=1.0)
    warning = _project_facts_if_canonical(deps, result)
    assert warning is not None
    assert "unmapped" in warning.lower() or "0 fact" in warning.lower()
