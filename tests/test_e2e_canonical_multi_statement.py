"""Phase 2 step 2.9 — multi-statement canonical-mode E2E.

A single run can have multiple templates active (one per statement
type).  This test imports four templates into one DB and exercises:

  - facts API accepts writes targeting concepts across all four;
  - cascade per-template runs independently;
  - exporter writes correct cells in each statement's workbook.

The Excel files stay independent here (Phase 1's exporter takes a
single template at a time).  The merger that combines them into one
final ``filled.xlsx`` is the existing legacy ``workbook_merger.py``
path — unchanged by the canonical model and out of scope here.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from concept_model.cascade import recompute_after_turn
from concept_model.exporter import export_run_to_xlsx
from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from db.schema import init_db


REPO = Path(__file__).resolve().parent.parent
COMPANY = REPO / "XBRL-template-MFRS" / "Company"


STATEMENTS = [
    ("sofp", "01-SOFP-CuNonCu.xlsx",        "mfrs-company-sofp-cunoncu-v1"),
    ("sopl", "03-SOPL-Function.xlsx",       "mfrs-company-sopl-function-v1"),
    ("soci", "05-SOCI-BeforeTax.xlsx",      "mfrs-company-soci-beforetax-v1"),
    ("socf", "07-SOCF-Indirect.xlsx",       "mfrs-company-socf-indirect-v1"),
]


def test_canonical_e2e_company_4_statements(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XBRL_CANONICAL_MODE", "1")
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db
    init_db(db)

    # Import every statement's template into the same DB.
    for name, filename, expected_tid in STATEMENTS:
        fixture = COMPANY / filename
        tree = parse_template(str(fixture))
        assert tree.template_id == expected_tid
        jp = tmp_path / f"{name}.json"
        jp.write_text(json.dumps(tree.to_json(), sort_keys=True),
                       encoding="utf-8")
        _ct_tid = import_template(db, jp)
        import_company_targets(db, _ct_tid)

    # Stash a run row.
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "multi.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    client = TestClient(srv.app)

    # Post one leaf fact per statement via the API.
    probe_value = 7777.0
    leaves: dict[str, tuple[str, str, int]] = {}
    for name, _filename, tid in STATEMENTS:
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT concept_uuid, render_sheet, render_row "
                "FROM concept_nodes WHERE template_id = ? AND kind = 'LEAF' "
                "ORDER BY render_sheet, render_row LIMIT 1",
                (tid,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        uid, sheet, srow = row[0], row[1], int(row[2])
        leaves[name] = (uid, sheet, srow)
        r = client.post(
            f"/api/runs/{run_id}/facts",
            json={
                "concept_uuid": uid, "period": "CY",
                "entity_scope": "Company", "value": probe_value,
                "value_status": "observed", "source": f"multi {name}",
            },
        )
        assert r.status_code == 200, r.text

    # Cascade picks up each template's COMPUTED parents independently.
    recompute_after_turn(db, run_id)

    # Export each statement and verify its leaf landed.
    for name, filename, _tid in STATEMENTS:
        uid, sheet, srow = leaves[name]
        work = tmp_path / f"filled-{name}.xlsx"
        shutil.copyfile(COMPANY / filename, work)
        export_run_to_xlsx(db, run_id, str(work))

        wb = openpyxl.load_workbook(str(work), data_only=False)
        cell = wb[sheet][f"B{srow}"]
        assert cell.value == probe_value, (
            f"{name}: expected {probe_value} at {sheet}!B{srow}, "
            f"got {cell.value!r}"
        )

    # Concepts endpoint surfaces concepts from all four templates.
    payload = client.get(f"/api/runs/{run_id}/concepts").json()
    seen_templates = {c["template_id"] for c in payload["concepts"]}
    assert len(seen_templates) == 4, seen_templates
