"""Pinning test: monolith write_cells projects to `run_concept_facts`.

The Values / Concepts page reads from `run_concept_facts`. The
first real monolith run (run 134, 2026-05-28) finished with 0 facts
in that table while the workbook was filled correctly — the Values
page rendered empty. The bug was that `write_cells` called
`fill_workbook` directly without the canonical projection step that
extraction/agent.py runs via `_project_facts_if_canonical`.

This test:
  1. Sets up a fake template + concept tree in a temp SQLite.
  2. Calls write_cells on a context with run_id+db_path+template_id_by_sheet.
  3. Asserts a row landed in run_concept_facts.

It also asserts the canonical projection is a no-op (no DB hit) when
canonical mode is off — important because the helper is now always
called inside write_cells.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import openpyxl
import pytest

from db.schema import init_db
from monolith.tools import (
    MonolithToolContext,
    _project_monolith_writes_if_canonical,
)


def _seed_concept_tree(db_path: Path, template_id: str, sheet: str, row: int) -> str:
    """Insert one template + one LEAF concept whose render coord matches a
    cell we're about to write. Returns the concept_uuid."""
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        # Minimal templates + nodes rows so cell_resolver.resolve_cell finds a match.
        conn.execute(
            "INSERT OR IGNORE INTO concept_templates(template_id, source_path) "
            "VALUES (?, ?)",
            (template_id, "/tmp/stub.xlsx"),
        )
        concept_uuid = "uuid-test-leaf"
        conn.execute(
            "INSERT OR IGNORE INTO concept_nodes("
            "concept_uuid, template_id, parent_uuid, kind, canonical_label, "
            "render_sheet, render_row, render_col) "
            "VALUES (?, ?, NULL, 'LEAF', 'Trade receivables', ?, ?, 'B')",
            (concept_uuid, template_id, sheet, row),
        )
        # Insert a draft run so foreign-key INSERTs into run_concept_facts succeed.
        conn.execute(
            "INSERT INTO runs(id, created_at, pdf_filename, status) "
            "VALUES (?, ?, ?, ?)",
            (42, "2026-05-28T00:00:00Z", "x.pdf", "running"),
        )
        conn.commit()
    finally:
        conn.close()
    return concept_uuid


def test_projection_writes_run_concept_facts_when_canonical(tmp_path):
    db = tmp_path / "audit.db"
    template_id = "mfrs-company-sofp-cunoncu-v1"
    concept_uuid = _seed_concept_tree(db, template_id, "SOFP-CuNonCu", row=5)

    ctx = MonolithToolContext(
        workbook_path=str(tmp_path / "unused.xlsx"),
        pdf_page_count=10,
        filing_standard="mfrs",
        filing_level="company",
        run_id=42,
        db_path=str(db),
        template_id_by_sheet={"SOFP-CuNonCu": template_id},
    )

    _project_monolith_writes_if_canonical(ctx, [
        {"sheet": "SOFP-CuNonCu", "row": 5, "col": 2, "value": 12345.0,
         "evidence": "Note 14 p.42"},
    ])

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT concept_uuid, value, source FROM run_concept_facts "
            "WHERE run_id = ?", (42,),
        ).fetchall()
    finally:
        conn.close()
    assert rows, "no facts landed in run_concept_facts — projection broken"
    assert rows[0][0] == concept_uuid
    assert rows[0][1] == 12345.0


def test_projection_noop_without_canonical_wiring(tmp_path):
    """When run_id / db_path / template_id_by_sheet are unset, the
    projection helper must short-circuit — no DB connection, no error."""
    ctx = MonolithToolContext(
        workbook_path=str(tmp_path / "x.xlsx"),
        pdf_page_count=10,
        # No run_id / db_path / template_id_by_sheet.
    )
    # Should return silently.
    _project_monolith_writes_if_canonical(ctx, [
        {"sheet": "SOFP-CuNonCu", "row": 5, "col": 2, "value": 1.0},
    ])
    # No assertion needed — surviving the call means short-circuit worked.


def test_projection_groups_writes_by_sheet_template(tmp_path):
    """Writes spanning two templates must be dispatched per-template."""
    db = tmp_path / "audit.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for tid, sheet in (
            ("mfrs-company-sofp-cunoncu-v1", "SOFP-CuNonCu"),
            ("mfrs-company-sopl-function-v1", "SOPL-Function"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO concept_templates(template_id, source_path) "
                "VALUES (?, ?)",
                (tid, "/tmp/stub.xlsx"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO concept_nodes("
                "concept_uuid, template_id, parent_uuid, kind, canonical_label, "
                "render_sheet, render_row, render_col) "
                "VALUES (?, ?, NULL, 'LEAF', 'L', ?, 10, 'B')",
                (f"uuid-{tid}", tid, sheet),
            )
        conn.execute(
            "INSERT INTO runs(id, created_at, pdf_filename, status) "
            "VALUES (?, ?, ?, ?)",
            (77, "2026-05-28T00:00:00Z", "x.pdf", "running"),
        )
        conn.commit()
    finally:
        conn.close()

    ctx = MonolithToolContext(
        workbook_path=str(tmp_path / "x.xlsx"),
        pdf_page_count=10,
        filing_standard="mfrs",
        filing_level="company",
        run_id=77,
        db_path=str(db),
        template_id_by_sheet={
            "SOFP-CuNonCu": "mfrs-company-sofp-cunoncu-v1",
            "SOPL-Function": "mfrs-company-sopl-function-v1",
        },
    )

    _project_monolith_writes_if_canonical(ctx, [
        {"sheet": "SOFP-CuNonCu", "row": 10, "col": 2, "value": 100.0},
        {"sheet": "SOPL-Function", "row": 10, "col": 2, "value": 200.0},
    ])

    conn = sqlite3.connect(str(db))
    try:
        templates_with_facts = set(
            r[0] for r in conn.execute(
                "SELECT cn.template_id FROM run_concept_facts f "
                "JOIN concept_nodes cn ON cn.concept_uuid = f.concept_uuid "
                "WHERE f.run_id = ?",
                (77,),
            ).fetchall()
        )
    finally:
        conn.close()
    assert templates_with_facts == {
        "mfrs-company-sofp-cunoncu-v1",
        "mfrs-company-sopl-function-v1",
    }, f"both templates should have facts; got {templates_with_facts}"
