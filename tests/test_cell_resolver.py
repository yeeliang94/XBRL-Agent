"""Phase B5/B6 — reverse cell→fact resolver + write projection.

The extraction agent writes (sheet, row, col, value) into a scratch xlsx. In
canonical mode those writes must also be projected into run_concept_facts. The
linchpin is `resolve_cell`, which maps a written cell back to its
(concept_uuid, period, entity_scope): via concept_targets for Group/matrix
templates, and via the Company B=CY/C=PY convention for linear Company filings.
`project_writes` applies that resolution and writes facts through apply_fact.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
GR_SOFP = REPO / "XBRL-template-MFRS" / "Group" / "01-SOFP-CuNonCu.xlsx"


def _import(db_path: Path, xlsx: Path, *, group: bool) -> str:
    from concept_model.parser import parse_template
    from concept_model.importer import import_template, import_group_targets

    tree = parse_template(str(xlsx))
    jp = db_path.parent / f"{xlsx.parent.name}-{xlsx.stem}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db_path, jp)
    if group and tree.shape != "matrix":
        import_group_targets(db_path, tid)
    return tid


@pytest.fixture
def db(tmp_path: Path):
    from db.schema import init_db

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    co_id = _import(db_path, CO_SOFP, group=False)
    gr_id = _import(db_path, GR_SOFP, group=True)

    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?, ?, ?, ?)",
        ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return db_path, run_id, co_id, gr_id


def test_resolve_company_linear_cy_and_py(db):
    db_path, _run, co_id, _gr = db
    from concept_model.cell_resolver import resolve_cell

    conn = sqlite3.connect(str(db_path))
    try:
        # A LEAF concept on the SOFP face sheet.
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]

        cy = resolve_cell(conn, co_id, "SOFP-CuNonCu", leaf, 2)
        py = resolve_cell(conn, co_id, "SOFP-CuNonCu", leaf, 3)
    finally:
        conn.close()

    assert cy is not None and cy[1:] == ("CY", "Company")
    assert py is not None and py[1:] == ("PY", "Company")
    assert cy[0] == py[0]  # same concept, different period


def test_resolve_group_uses_targets(db):
    db_path, _run, _co, gr_id = db
    from concept_model.cell_resolver import resolve_cell
    from openpyxl.utils import column_index_from_string

    conn = sqlite3.connect(str(db_path))
    try:
        # Pick a Group target row (col B = Group CY).
        tgt = conn.execute(
            "SELECT ct.target_sheet, ct.target_row FROM concept_targets ct "
            "JOIN concept_nodes n ON n.concept_uuid = ct.concept_uuid "
            "WHERE n.template_id=? AND ct.target_col='B' LIMIT 1",
            (gr_id,),
        ).fetchone()
        sheet, row = tgt[0], int(tgt[1])
        b = resolve_cell(conn, gr_id, sheet, row, column_index_from_string("B"))
        d = resolve_cell(conn, gr_id, sheet, row, column_index_from_string("D"))
    finally:
        conn.close()

    assert b is not None and b[1:] == ("CY", "Group")
    assert d is not None and d[1:] == ("CY", "Company")


def test_fill_workbook_resolved_writes_feed_projection(db, tmp_path):
    """End-to-end seam: fill_workbook emits resolved_writes whose
    coordinates project cleanly into facts (no LLM involved)."""
    db_path, run_id, co_id, _gr = db
    from tools.fill_workbook import fill_workbook
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf_label = conn.execute(
            "SELECT canonical_label, render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()
    finally:
        conn.close()
    label, row = leaf_label[0], int(leaf_label[1])

    out = tmp_path / "out.xlsx"
    facts = [
        {"sheet": "SOFP-CuNonCu", "row": row, "col": 2, "value": 555.0,
         "evidence": "p3"},
    ]
    result = fill_workbook(str(CO_SOFP), str(out), facts, filing_level="company")
    assert result.success
    assert result.resolved_writes, "expected resolved_writes for canonical projection"

    proj = project_writes(db_path, run_id, co_id, result.resolved_writes,
                          filing_level="company")
    assert proj.projected == 1
    assert not proj.has_gaps
    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY'",
            (run_id,),
        ).fetchone()[0]
        assert val == 555.0
    finally:
        conn.close()


def test_project_writes_lands_facts(db):
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    writes = [
        {"sheet": "SOFP-CuNonCu", "row": leaf, "col": 2, "value": 999.0,
         "evidence": "Page 3"},
    ]
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")
    assert proj.projected == 1

    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY' "
            "AND entity_scope='Company'",
            (run_id,),
        ).fetchone()[0]
        assert val == 999.0
    finally:
        conn.close()


def test_project_writes_reports_unmapped_cells(db):
    """A write to an evidence column (col 4 on a Company linear sheet) doesn't
    resolve to a concept — it's reported as skipped, not silently dropped."""
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes WHERE template_id=? "
            "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    writes = [{"sheet": "SOFP-CuNonCu", "row": leaf, "col": 4, "value": 1.0}]
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")
    assert proj.projected == 0
    assert proj.has_gaps
    assert len(proj.skipped) == 1


def test_non_numeric_value_rejects_one_cell_without_aborting_batch(db):
    """Run-49 SOCI regression: a text/title row the agent wrote resolves to a
    concept (it has a render_row), so it's NOT skipped — and facts_api.FactWrite
    .value is Optional[float], so constructing it raises pydantic ValidationError.

    That error must be isolated to the offending cell: the numeric facts in the
    same batch still land, and the projection CALL does not raise (which would
    flip projection_failed -> FATAL and roll back the whole statement, losing the
    valid profit rows — the exact run-49 failure).
    """
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaves = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 2",
            (co_id,),
        ).fetchall()
    finally:
        conn.close()
    numeric_row, text_row = int(leaves[0][0]), int(leaves[1][0])

    writes = [
        # A genuine numeric fact (the SOCI profit row equivalent).
        {"sheet": "SOFP-CuNonCu", "row": numeric_row, "col": 2, "value": 9_078_749.0,
         "evidence": "p12"},
        # A text/title row that resolves to a concept but carries prose.
        {"sheet": "SOFP-CuNonCu", "row": text_row, "col": 2,
         "value": "Statement of comprehensive income", "evidence": "p12"},
    ]
    # Must NOT raise — the whole point of the fix.
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")

    assert proj.projected == 1, "the numeric fact must still land"
    assert len(proj.rejected) == 1, "the text cell is rejected per-cell"
    assert "non-numeric" in proj.rejected[0].lower()

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY' "
            "AND entity_scope='Company'",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == [9_078_749.0]
