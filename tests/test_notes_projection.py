"""Numeric notes: writer capture + projection + Values-tab exclusion.

PLAN-notes-template-registry Phases 3-4 (Track B). Covers:
  * the writer's numeric column mapping + numeric_cells manifest (Step 9a);
  * GET /notes_cells projecting numeric sheets from run_concept_facts (Step 7);
  * GET /concepts excluding numeric notes so they don't duplicate into the
    Values tab (Step 10).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from db.schema import init_db
from concept_model.bootstrap import import_all_notes_templates
from notes_types import NotesTemplateType, notes_template_path
from concept_model.parser import _derive_template_id


_ISSUED_CAPITAL_TID = "mfrs-company-notes-issuedcapital-v1"


# ---------------------------------------------------------------------------
# Step 9a — writer numeric capture
# ---------------------------------------------------------------------------

def test_numeric_write_cols_mapping():
    from notes.writer import _numeric_write_cols

    company = _numeric_write_cols({"company_cy": 10, "company_py": 9}, "company")
    assert company == [(2, 10), (3, 9)]
    # Bare cy/py shorthand also resolves on company filings.
    assert _numeric_write_cols({"cy": 5, "py": 4}, "company") == [(2, 5), (3, 4)]

    group = _numeric_write_cols(
        {"group_cy": 1, "group_py": 2, "company_cy": 3, "company_py": 4}, "group"
    )
    assert group == [(2, 1), (3, 2), (4, 3), (5, 4)]


def test_writer_emits_numeric_cells(tmp_path):
    """write_notes_workbook returns a numeric_cells manifest for numeric rows."""
    from tools.template_reader import read_template
    from notes.writer import write_notes_workbook
    from notes.payload import NotesPayload

    tpl = notes_template_path(
        NotesTemplateType.ISSUED_CAPITAL, level="company", standard="mfrs"
    )
    sheet = "Notes-Issuedcapital"
    # Pick a real, non-abstract col-A label to target.
    label = next(
        f.value for f in read_template(str(tpl), sheet=sheet)
        if f.col == 1 and f.value and not f.is_abstract
    )

    out = tmp_path / "filled.xlsx"
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=[NotesPayload(
            chosen_row_label=label,
            content="",
            evidence="p5",
            source_pages=[5],
            numeric_values={"company_cy": 1234.0, "company_py": 1000.0},
            parent_note={"number": "1", "title": "Issued capital"},
        )],
        output_path=str(out),
        filing_level="company",
        sheet_name=sheet,
    )
    assert result.success
    # Two value cells captured (B=CY, C=PY), none in the prose manifest.
    assert result.cells_written == []
    cols = sorted(c["col"] for c in result.numeric_cells)
    assert cols == [2, 3]
    assert {c["value"] for c in result.numeric_cells} == {1234.0, 1000.0}


# ---------------------------------------------------------------------------
# Steps 7 + 10 — projection + Values exclusion (via the API)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_with_numeric_run(tmp_path, monkeypatch):
    import server as server_module
    from fastapi.testclient import TestClient

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    import_all_notes_templates(server_module.AUDIT_DB_PATH)

    conn = sqlite3.connect(str(server_module.AUDIT_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # A run that targeted the numeric Issued Capital note.
        cfg = {
            "filing_standard": "mfrs",
            "filing_level": "company",
            "notes_to_run": ["ISSUED_CAPITAL"],
        }
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, run_config_json, started_at) "
            "VALUES ('2026-06-17T00:00:00Z', 'x.pdf', 'completed', 'sess', "
            "'/tmp/sess', ?, '2026-06-17T00:00:00Z')",
            (json.dumps(cfg),),
        )
        run_id = int(cur.lastrowid)

        # Seed one CY/Company fact on a LEAF concept of the numeric template.
        leaf = conn.execute(
            "SELECT concept_uuid, render_row FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'LEAF' ORDER BY render_row LIMIT 1",
            (_ISSUED_CAPITAL_TID,),
        ).fetchone()
        assert leaf is not None, "numeric notes concepts must be imported"
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', 4242.0, 'observed', '2026-06-17T00:00:00Z')",
            (run_id, leaf["concept_uuid"]),
        )
        conn.commit()
        seeded_row = int(leaf["render_row"])
    finally:
        conn.close()

    return TestClient(server_module.app), run_id, seeded_row


def test_numeric_sheet_projects_with_values(client_with_numeric_run):
    client, run_id, seeded_row = client_with_numeric_run

    body = client.get(f"/api/runs/{run_id}/notes_cells").json()
    sheets = {s["sheet"]: s for s in body["sheets"]}
    assert "Notes-Issuedcapital" in sheets

    cap = sheets["Notes-Issuedcapital"]
    assert cap["kind"] == "numeric"
    rows = {r["row"]: r for r in cap["rows"]}
    # The seeded fact is projected onto its row; other rows are blank (None).
    assert rows[seeded_row]["kind"] == "numeric"
    assert rows[seeded_row]["values"]["cy"] == 4242.0
    assert rows[seeded_row]["concept_uuid"]
    blanks = [r for r in cap["rows"] if r["values"]["cy"] is None]
    assert blanks  # full template → unfilled rows present as blanks


def test_values_tab_excludes_numeric_notes(client_with_numeric_run):
    """The numeric notes template carries facts but must NOT surface in the
    Values/Concepts tree (decision §9.3)."""
    client, run_id, _ = client_with_numeric_run

    body = client.get(f"/api/runs/{run_id}/concepts").json()
    template_ids = {c["template_id"] for c in body["concepts"]}
    assert _ISSUED_CAPITAL_TID not in template_ids
    # In fact this run touched ONLY the numeric note → Values tree is empty.
    assert body["concepts"] == []


# ---------------------------------------------------------------------------
# Peer-review HIGH — numeric-note edits must reach the download.
# ---------------------------------------------------------------------------

def test_numeric_facts_overlay_writes_edited_value(tmp_path):
    """overlay_numeric_facts_into_workbook writes a run's numeric-note fact onto
    its target cell — so a PATCH /facts edit reaches the downloaded xlsx even
    though the agent's on-disk workbook is unchanged."""
    import shutil
    import openpyxl
    from openpyxl.utils import column_index_from_string
    from notes.persistence import overlay_numeric_facts_into_workbook

    db = tmp_path / "audit.sqlite"
    init_db(db)
    import_all_notes_templates(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, started_at) VALUES "
            "('2026-06-17T00:00:00Z', 'x.pdf', 'completed', 's', '/tmp/s', "
            "'2026-06-17T00:00:00Z')"
        )
        run_id = int(cur.lastrowid)
        # A LEAF concept of the numeric template with a Company/CY target cell.
        tgt = conn.execute(
            "SELECT t.concept_uuid, t.target_sheet, t.target_row, t.target_col "
            "FROM concept_targets t JOIN concept_nodes n "
            "ON n.concept_uuid = t.concept_uuid "
            "WHERE n.template_id = ? AND n.kind = 'LEAF' "
            "AND t.entity_scope = 'Company' AND t.period = 'CY' LIMIT 1",
            (_ISSUED_CAPITAL_TID,),
        ).fetchone()
        assert tgt is not None
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) VALUES "
            "(?, ?, 'CY', 'Company', 98765.0, 'observed', '2026-06-17T00:00:00Z')",
            (run_id, tgt["concept_uuid"]),
        )
        conn.commit()
    finally:
        conn.close()

    # Start from the pristine template (stand-in for the merged workbook's
    # numeric sheet — the agent's on-disk copy that does NOT have the edit).
    tpl = notes_template_path(
        NotesTemplateType.ISSUED_CAPITAL, level="company", standard="mfrs"
    )
    wb_in = tmp_path / "merged.xlsx"
    shutil.copy(str(tpl), str(wb_in))

    out = overlay_numeric_facts_into_workbook(
        xlsx_path=wb_in, run_id=run_id, db_path=str(db)
    )
    assert out != wb_in  # a fresh temp file was produced

    wb = openpyxl.load_workbook(str(out))
    ws = wb[tgt["target_sheet"]]
    cell = ws.cell(
        row=int(tgt["target_row"]),
        column=column_index_from_string(tgt["target_col"]),
    )
    assert cell.value == 98765.0


# ---------------------------------------------------------------------------
# Step 9 — coordinator capture wiring (writer manifest → run_concept_facts)
# ---------------------------------------------------------------------------

def test_coordinator_projects_numeric_cells_to_facts(tmp_path):
    """The coordinator's numeric-capture helper resolves a writer
    ``numeric_cells`` manifest into ``run_concept_facts`` — the wiring between
    the writer and ``project_writes`` that the per-template e2e never isolates.
    """
    import asyncio

    from notes.coordinator import (
        _project_numeric_notes_facts,
        NotesRunConfig,
        NotesAgentResult,
    )

    db = tmp_path / "audit.sqlite"
    init_db(db)
    import_all_notes_templates(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, started_at) VALUES "
            "('2026-06-17T00:00:00Z', 'x.pdf', 'completed', 's', '/tmp/s', "
            "'2026-06-17T00:00:00Z')"
        )
        run_id = int(cur.lastrowid)
        # A real LEAF row of the numeric template, with its Company/CY target
        # cell (column letter) — that's the cell the writer manifest targets.
        tgt = conn.execute(
            "SELECT n.render_row AS row, t.target_col AS col "
            "FROM concept_nodes n JOIN concept_targets t "
            "ON t.concept_uuid = n.concept_uuid "
            "WHERE n.template_id = ? AND n.kind = 'LEAF' "
            "AND t.entity_scope = 'Company' AND t.period = 'CY' "
            "ORDER BY n.render_row LIMIT 1",
            (_ISSUED_CAPITAL_TID,),
        ).fetchone()
        assert tgt is not None
        conn.commit()
        seeded_row = int(tgt["row"])
        from openpyxl.utils import column_index_from_string
        seeded_col = column_index_from_string(tgt["col"])
    finally:
        conn.close()

    config = NotesRunConfig(
        pdf_path="x.pdf",
        output_dir=str(tmp_path),
        model=None,
        filing_level="company",
        filing_standard="mfrs",
        run_id=run_id,
        audit_db_path=str(db),
    )
    result = NotesAgentResult(
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        status="succeeded",
        numeric_cells=[{
            "sheet": "Notes-Issuedcapital",
            "row": seeded_row,
            "col": seeded_col,
            "value": 55555.0,
            "evidence": "p7",
        }],
    )

    projection = asyncio.run(_project_numeric_notes_facts(config, result))
    assert projection is not None
    assert projection.projected == 1

    conn = sqlite3.connect(str(db))
    try:
        fact = conn.execute(
            "SELECT f.value FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ? AND n.template_id = ? "
            "AND f.period = 'CY' AND f.entity_scope = 'Company' "
            "AND n.render_row = ?",
            (run_id, _ISSUED_CAPITAL_TID, seeded_row),
        ).fetchone()
    finally:
        conn.close()
    assert fact is not None and fact[0] == 55555.0


def test_coordinator_numeric_capture_noop_without_cells(tmp_path):
    """No numeric cells → the helper returns None and writes nothing."""
    import asyncio
    from notes.coordinator import (
        _project_numeric_notes_facts,
        NotesRunConfig,
        NotesAgentResult,
    )

    db = tmp_path / "audit.sqlite"
    init_db(db)
    config = NotesRunConfig(
        pdf_path="x.pdf", output_dir=str(tmp_path), model=None,
        run_id=1, audit_db_path=str(db),
    )
    result = NotesAgentResult(
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        status="succeeded",
        numeric_cells=[],
    )
    assert asyncio.run(_project_numeric_notes_facts(config, result)) is None


def test_numeric_facts_overlay_noop_without_facts(tmp_path):
    """No numeric-note facts → the workbook is returned unchanged (no temp)."""
    from notes.persistence import overlay_numeric_facts_into_workbook

    db = tmp_path / "audit.sqlite"
    init_db(db)
    import_all_notes_templates(db)
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, started_at) VALUES "
            "('2026-06-17T00:00:00Z', 'x.pdf', 'completed', 's', '/tmp/s', "
            "'2026-06-17T00:00:00Z')"
        )
        run_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    import shutil
    tpl = notes_template_path(
        NotesTemplateType.ISSUED_CAPITAL, level="company", standard="mfrs"
    )
    wb_in = tmp_path / "merged.xlsx"
    shutil.copy(str(tpl), str(wb_in))
    out = overlay_numeric_facts_into_workbook(
        xlsx_path=wb_in, run_id=run_id, db_path=str(db)
    )
    assert out == wb_in  # unchanged, no temp produced
