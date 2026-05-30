"""Phase 2 steps 2.1-2.8 — expand canonical-mode coverage to all 7
remaining Company face/sub templates.

The parser + importer + exporter are template-agnostic (verified in
Phase 0's cross-template smoke test).  This module pins that
generality with a parametrized matrix: for each template, prove that

  parse  → import  → kind counts plausible
                  → seed a known leaf fact
                  → cascade
                  → exporter writes the value to the right cell

without rebuilding the integration scaffolding seven times.

Auto-correction stays DISABLED per the phase pre-gate (the
``XBRL_CANONICAL_MODE`` flag is set by the caller; this test doesn't
exercise the correction pipeline).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import openpyxl
import pytest

from concept_model.cascade import recompute_after_turn
from concept_model.exporter import export_run_to_xlsx
from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from db.schema import init_db


REPO = Path(__file__).resolve().parent.parent
COMPANY = REPO / "XBRL-template-MFRS" / "Company"


# Templates added in Phase 2.  Each entry pins:
#   filename            — the on-disk xlsx
#   expected_template_id — the slug the parser mints (also the JSON file
#                          name when --all is run)
#   probe_sheet, probe_row — a known LEAF row we can seed + read back
PHASE2_TEMPLATES = [
    {
        "id": "sofp_order_of_liquidity",
        "filename": "02-SOFP-OrderOfLiquidity.xlsx",
        "template_id": "mfrs-company-sofp-orderofliquidity-v1",
    },
    {
        "id": "sopl_function",
        "filename": "03-SOPL-Function.xlsx",
        "template_id": "mfrs-company-sopl-function-v1",
    },
    {
        "id": "sopl_nature",
        "filename": "04-SOPL-Nature.xlsx",
        "template_id": "mfrs-company-sopl-nature-v1",
    },
    {
        "id": "soci_before_tax",
        "filename": "05-SOCI-BeforeTax.xlsx",
        "template_id": "mfrs-company-soci-beforetax-v1",
    },
    {
        "id": "soci_net_of_tax",
        "filename": "06-SOCI-NetOfTax.xlsx",
        "template_id": "mfrs-company-soci-netoftax-v1",
    },
    {
        "id": "socf_indirect",
        "filename": "07-SOCF-Indirect.xlsx",
        "template_id": "mfrs-company-socf-indirect-v1",
    },
    {
        "id": "socf_direct",
        "filename": "08-SOCF-Direct.xlsx",
        "template_id": "mfrs-company-socf-direct-v1",
    },
]


def _first_leaf_uuid(db: Path, template_id: str) -> tuple[str, str, int]:
    """Return (concept_uuid, render_sheet, render_row) for the first
    LEAF row in this template — used as the probe cell for E2E tests.
    """
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT concept_uuid, render_sheet, render_row "
            "FROM concept_nodes WHERE template_id = ? AND kind = 'LEAF' "
            "ORDER BY render_sheet, render_row LIMIT 1",
            (template_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no LEAF concept in {template_id}"
    return row[0], row[1], int(row[2])


# ---------------------------------------------------------------------------
# Step 2.1-2.8a — import each template into a fresh DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", PHASE2_TEMPLATES, ids=lambda s: s["id"])
def test_import_phase2_template(tmp_path: Path, spec: dict) -> None:
    fixture = COMPANY / spec["filename"]
    if not fixture.is_file():
        pytest.skip(f"template missing: {fixture}")

    db = tmp_path / "xbrl.db"
    init_db(db)

    tree = parse_template(str(fixture))
    assert tree.template_id == spec["template_id"]

    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)
    import_company_targets(db, template_id)
    assert template_id == spec["template_id"]

    # Plausibility on row counts — three kinds, all present.
    conn = sqlite3.connect(str(db))
    try:
        counts = dict(conn.execute(
            "SELECT kind, COUNT(*) FROM concept_nodes "
            "WHERE template_id = ? GROUP BY kind",
            (template_id,),
        ).fetchall())
    finally:
        conn.close()
    # Every face template has at least one ABSTRACT header, ≥1 LEAF
    # row, and ≥1 COMPUTED total.  Tighter bounds are template-specific
    # and would couple the test to label drift in the SSM linkbase.
    assert counts.get("ABSTRACT", 0) >= 1, counts
    assert counts.get("LEAF", 0) >= 1, counts
    assert counts.get("COMPUTED", 0) >= 1, counts


# ---------------------------------------------------------------------------
# Step 2.1-2.8b — minimal canonical-mode E2E per template.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", PHASE2_TEMPLATES, ids=lambda s: s["id"])
def test_canonical_e2e_phase2_template(tmp_path: Path, spec: dict) -> None:
    """Drive parse → import → seed → cascade → export per template.

    We do NOT involve the LLM — that's a Phase-2 production smoke
    item.  This test pins the canonical pipeline's mechanical
    correctness across the seven templates.
    """
    fixture = COMPANY / spec["filename"]
    if not fixture.is_file():
        pytest.skip(f"template missing: {fixture}")

    db = tmp_path / "xbrl.db"
    init_db(db)

    tree = parse_template(str(fixture))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    _ct_tid = import_template(db, jp)
    import_company_targets(db, _ct_tid)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "p2.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Seed the first LEAF row with a recognisable value.
    leaf_uuid, sheet, row = _first_leaf_uuid(db, spec["template_id"])
    probe_value = 12345.67
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'p2', "
            "'2026-05-21Z')",
            (run_id, leaf_uuid, probe_value),
        )
        conn.commit()
    finally:
        conn.close()

    recompute_after_turn(db, run_id)

    # Copy template + export.
    work = tmp_path / "filled.xlsx"
    shutil.copyfile(fixture, work)
    export_run_to_xlsx(db, run_id, str(work))

    # The probe value lands in the right cell.
    wb = openpyxl.load_workbook(str(work), data_only=False)
    cell = wb[sheet][f"B{row}"]
    assert cell.value == probe_value, (
        f"{spec['id']}: expected {probe_value} at {sheet}!B{row}, "
        f"got {cell.value!r}"
    )
