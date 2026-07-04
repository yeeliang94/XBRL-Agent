"""Tests for the facts → mTool fill-instructions bridge (mtool/exporter.py).

Fixture pattern mirrors tests/test_canonical_export.py: parse + import a real
template, seed a run row and facts, then assert the emitted fill doc.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import (
    import_company_targets,
    import_group_targets,
    import_template,
)
from concept_model.parser import parse_template
from db.schema import init_db
from mtool.exporter import apply_column_map, build_fill_doc
from mtool.offline_fill import validate_input

REPO = Path(__file__).resolve().parent.parent
SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
SOFP_GROUP = REPO / "XBRL-template-MFRS" / "Group" / "01-SOFP-CuNonCu.xlsx"


def _init_run(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-07-05T00:00:00Z", "x.pdf", "completed",
             "2026-07-05T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _import(db: Path, fixture: Path, *, group: bool = False) -> str:
    tree = parse_template(str(fixture))
    jp = db.parent / f"{fixture.stem}_{'g' if group else 'c'}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)
    import_company_targets(db, template_id)
    if group:
        import_group_targets(db, template_id)
    return template_id


def _leaf_at(db: Path, sheet: str, row: int) -> tuple[str, str]:
    """Return (uuid, canonical_label) for a LEAF concept at a coord."""
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT concept_uuid, canonical_label FROM concept_nodes "
            "WHERE render_sheet = ? AND render_row = ? AND kind = 'LEAF'",
            (sheet, row),
        ).fetchone()
    finally:
        conn.close()
    assert r is not None, f"no LEAF at {sheet}!{row}"
    return r[0], r[1]


def _find_leaf(db: Path, sheet_like: str = "Sub") -> tuple[str, str, str]:
    """Return (uuid, sheet, label) of an arbitrary LEAF on a sub-sheet."""
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT concept_uuid, render_sheet, canonical_label "
            "FROM concept_nodes WHERE kind = 'LEAF' AND render_sheet LIKE ? "
            "ORDER BY render_row LIMIT 1",
            (f"%{sheet_like}%",),
        ).fetchone()
    finally:
        conn.close()
    assert r is not None
    return r[0], r[1], r[2]


def _seed(db: Path, run_id: int, uuid: str, *, period="CY",
          scope="Company", value=None, value_status="observed"):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts("
            "run_id, concept_uuid, period, entity_scope, value, "
            "value_status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, uuid, period, scope, value, value_status,
             "2026-07-05T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def company_db(tmp_path: Path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    _import(db, SOFP)
    run_id = _init_run(db)
    return db, run_id


# ---------------------------------------------------------------- core

def test_leaf_fact_becomes_a_write(company_db):
    db, run_id = company_db
    uuid, sheet, label = _find_leaf(db)
    _seed(db, run_id, uuid, value=1500)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert doc["writes"] == [
        {"sheet": sheet, "label": label,
         "column_role": "current_year", "value": 1500}]
    assert doc["strict"] is True
    assert doc["meta"]["counts"]["writes"] == 1


def test_cy_and_py_map_to_distinct_roles(company_db):
    db, run_id = company_db
    uuid, sheet, label = _find_leaf(db)
    _seed(db, run_id, uuid, period="CY", value=100)
    _seed(db, run_id, uuid, period="PY", value=90)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    roles = {w["column_role"]: w["value"] for w in doc["writes"]}
    assert roles == {"current_year": 100, "prior_year": 90}
    assert doc["sheets"][sheet]["columns"] == {
        "current_year": None, "prior_year": None}


def test_not_disclosed_is_counted_not_written(company_db):
    db, run_id = company_db
    uuid, _, _ = _find_leaf(db)
    _seed(db, run_id, uuid, value=None, value_status="not_disclosed")
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert doc["writes"] == []
    assert doc["meta"]["counts"]["excluded_not_disclosed"] == 1


def test_explicit_zero_is_written(company_db):
    db, run_id = company_db
    uuid, _, _ = _find_leaf(db)
    _seed(db, run_id, uuid, value=0, value_status="explicit_zero")
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert len(doc["writes"]) == 1
    assert doc["writes"][0]["value"] == 0


def test_computed_and_abstract_are_excluded(company_db):
    db, run_id = company_db
    # Seed a fact on a COMPUTED total row if one exists.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE kind='COMPUTED' "
            "LIMIT 1").fetchall()
    finally:
        conn.close()
    if rows:
        _seed(db, run_id, rows[0][0], value=9999)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    # No write should carry the COMPUTED value.
    assert all(w["value"] != 9999 for w in doc["writes"])


def test_scale_multiplies_values(company_db):
    db, run_id = company_db
    uuid, _, _ = _find_leaf(db)
    _seed(db, run_id, uuid, value=1500)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company", scale=1000)
    assert doc["writes"][0]["value"] == 1_500_000
    assert doc["meta"]["scale"] == 1000


def test_default_scale_is_identity(company_db):
    db, run_id = company_db
    uuid, _, _ = _find_leaf(db)
    _seed(db, run_id, uuid, value=1234.5)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert doc["writes"][0]["value"] == 1234.5
    assert doc["meta"]["scale"] == 1.0


def test_output_validates_after_column_map(company_db):
    db, run_id = company_db
    uuid, sheet, _ = _find_leaf(db)
    _seed(db, run_id, uuid, period="CY", value=100)
    _seed(db, run_id, uuid, period="PY", value=90)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    ready = apply_column_map(doc, {
        sheet: {"label_column": "A",
                "columns": {"current_year": "B", "prior_year": "C"}}})
    assert validate_input(ready) == []
    assert ready["meta"]["columns_unresolved"] is False


def test_apply_column_map_raises_on_missing_role(company_db):
    db, run_id = company_db
    uuid, sheet, _ = _find_leaf(db)
    _seed(db, run_id, uuid, period="CY", value=100)
    _seed(db, run_id, uuid, period="PY", value=90)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    with pytest.raises(ValueError, match="prior_year"):
        apply_column_map(doc, {
            sheet: {"label_column": "A",
                    "columns": {"current_year": "B"}}})  # PY missing


# ---------------------------------------------------------------- group

@pytest.fixture
def group_db(tmp_path: Path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    _import(db, SOFP_GROUP, group=True)
    run_id = _init_run(db)
    return db, run_id


def test_group_scopes_map_to_four_roles(group_db):
    db, run_id = group_db
    uuid, sheet, _ = _find_leaf(db)
    _seed(db, run_id, uuid, period="CY", scope="Group", value=100)
    _seed(db, run_id, uuid, period="PY", scope="Group", value=90)
    _seed(db, run_id, uuid, period="CY", scope="Company", value=80)
    _seed(db, run_id, uuid, period="PY", scope="Company", value=70)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="group")
    roles = {w["column_role"]: w["value"] for w in doc["writes"]}
    assert roles == {
        "group_current_year": 100, "group_prior_year": 90,
        "company_current_year": 80, "company_prior_year": 70}


def test_group_scope_dropped_on_company_filing(company_db):
    db, run_id = company_db
    uuid, _, _ = _find_leaf(db)
    _seed(db, run_id, uuid, period="CY", scope="Group", value=100)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert doc["writes"] == []
    assert doc["meta"]["counts"]["excluded_out_of_scope"] == 1


# ---------------------------------------------------------------- Step 3:
# end-to-end dry run. DB canonical_labels MUST resolve exactly against our own
# template (same SSM taxonomy vocabulary) — 0 fuzzy, 0 unresolved. A fuzzy hit
# here is exporter label drift, caught cheaply before the Windows box.

def test_db_labels_resolve_exactly_against_own_template(company_db, tmp_path):
    import shutil

    from mtool.offline_fill import main as fill_main

    db, run_id = company_db
    # Seed real, distinct sub-sheet line-item leaves (labels unique in col A,
    # so resolution is unambiguous — the point is to prove exact label match,
    # not to re-test the ambiguous-header guard).
    conn = sqlite3.connect(str(db))
    try:
        leaves = conn.execute(
            """
            SELECT concept_uuid, render_sheet, render_row, canonical_label
            FROM concept_nodes
            WHERE kind='LEAF' AND render_sheet LIKE '%Sub%'
              AND canonical_label IN (
                SELECT canonical_label FROM concept_nodes
                GROUP BY canonical_label HAVING COUNT(*) = 1)
            ORDER BY render_row LIMIT 6
            """).fetchall()
    finally:
        conn.close()
    assert len(leaves) == 6
    for i, (uuid, _, _, _) in enumerate(leaves):
        _seed(db, run_id, uuid, value=1000 + i)

    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    sheet = doc["meta"]["sheets_covered"][0]
    ready = apply_column_map(doc, {
        sheet: {"label_column": "A",
                "columns": {"current_year": "B", "prior_year": "C"}}})

    inp = tmp_path / "fill.json"
    inp.write_text(json.dumps(ready), encoding="utf-8")
    work = tmp_path / "template.xlsx"
    shutil.copyfile(SOFP, work)
    out = tmp_path / "filled.xlsx"
    report_path = tmp_path / "report.json"
    code = fill_main(["fill", "--workbook", str(work), "--input", str(inp),
                      "--output", str(out), "--report", str(report_path)])
    report = json.loads(report_path.read_text())

    assert report["fuzzy_matched"] == [], "DB label drifted from template"
    assert report["unresolved"] == []
    assert report["skipped_formula"] == []
    assert code == 0
    assert len(report["written"]) == 6
