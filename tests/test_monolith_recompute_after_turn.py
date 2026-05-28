"""Peer-review fix pinning test.

Bug: the monolith path projected leaf facts to `run_concept_facts` but
never called `recompute_after_turn`. The split path explicitly calls it
after extraction so COMPUTED parents (e.g. *Total assets*) populate
from the leaves. Without it, the monolith run's Values page shows leaf
rows only — totals stay blank.

This test:
  1. Seeds a fake template with one COMPUTED parent and two LEAF children.
  2. Calls the monolith projection helper on the two leaves.
  3. Calls `recompute_after_turn`.
  4. Asserts a fact landed for the COMPUTED parent (the cascade did its job).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.schema import init_db
from monolith.tools import (
    MonolithToolContext,
    _project_monolith_writes_if_canonical,
)


def _seed_parent_with_two_leaves(
    db_path: Path,
    template_id: str,
    sheet: str,
    *,
    leaf_a_row: int = 5,
    leaf_b_row: int = 6,
    parent_row: int = 10,
) -> dict:
    """Insert a template + a COMPUTED parent + two LEAF children.

    Parent = LEAF_A + LEAF_B via concept_edges. Returns the concept_uuids
    so the test can assert on them.
    """
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO concept_templates(template_id, source_path) "
            "VALUES (?, ?)",
            (template_id, "/tmp/stub.xlsx"),
        )
        parent_uuid = "uuid-parent-computed"
        leaf_a_uuid = "uuid-leaf-a"
        leaf_b_uuid = "uuid-leaf-b"
        # Parent first so the children's parent_uuid FK resolves.
        conn.execute(
            "INSERT OR IGNORE INTO concept_nodes("
            "concept_uuid, template_id, parent_uuid, kind, canonical_label, "
            "render_sheet, render_row, render_col) "
            "VALUES (?, ?, NULL, 'COMPUTED', 'Total parent', ?, ?, 'B')",
            (parent_uuid, template_id, sheet, parent_row),
        )
        conn.execute(
            "INSERT OR IGNORE INTO concept_nodes("
            "concept_uuid, template_id, parent_uuid, kind, canonical_label, "
            "render_sheet, render_row, render_col) "
            "VALUES (?, ?, ?, 'LEAF', 'Leaf A', ?, ?, 'B')",
            (leaf_a_uuid, template_id, parent_uuid, sheet, leaf_a_row),
        )
        conn.execute(
            "INSERT OR IGNORE INTO concept_nodes("
            "concept_uuid, template_id, parent_uuid, kind, canonical_label, "
            "render_sheet, render_row, render_col) "
            "VALUES (?, ?, ?, 'LEAF', 'Leaf B', ?, ?, 'B')",
            (leaf_b_uuid, template_id, parent_uuid, sheet, leaf_b_row),
        )
        # Wire the edges: parent = +1*leaf_a + +1*leaf_b.
        conn.execute(
            "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
            "VALUES (?, ?, 1.0)",
            (parent_uuid, leaf_a_uuid),
        )
        conn.execute(
            "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
            "VALUES (?, ?, 1.0)",
            (parent_uuid, leaf_b_uuid),
        )
        conn.execute(
            "INSERT INTO runs(id, created_at, pdf_filename, status) "
            "VALUES (?, ?, ?, ?)",
            (101, "2026-05-28T00:00:00Z", "x.pdf", "running"),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "parent": parent_uuid,
        "leaf_a": leaf_a_uuid,
        "leaf_b": leaf_b_uuid,
    }


def test_recompute_after_turn_fills_computed_parent_from_monolith_writes(tmp_path):
    db = tmp_path / "audit.db"
    template_id = "mfrs-company-sofp-cunoncu-v1"
    sheet = "SOFP-CuNonCu"
    uuids = _seed_parent_with_two_leaves(db, template_id, sheet)

    # Project two LEAF writes via the monolith projection helper. Values:
    # 100 and 200. The parent's expected computed value is 300.
    ctx = MonolithToolContext(
        workbook_path=str(tmp_path / "unused.xlsx"),
        pdf_page_count=10,
        filing_standard="mfrs",
        filing_level="company",
        run_id=101,
        db_path=str(db),
        template_id_by_sheet={sheet: template_id},
    )
    _project_monolith_writes_if_canonical(ctx, [
        {"sheet": sheet, "row": 5, "col": 2, "value": 100.0,
         "evidence": "p.10"},
        {"sheet": sheet, "row": 6, "col": 2, "value": 200.0,
         "evidence": "p.10"},
    ])

    # Sanity: leaves landed but parent does NOT exist yet.
    conn = sqlite3.connect(str(db))
    try:
        before = {
            r[0]: r[1] for r in conn.execute(
                "SELECT concept_uuid, value FROM run_concept_facts "
                "WHERE run_id = ?",
                (101,),
            ).fetchall()
        }
    finally:
        conn.close()
    assert before.get(uuids["leaf_a"]) == 100.0
    assert before.get(uuids["leaf_b"]) == 200.0
    assert uuids["parent"] not in before, (
        "parent should not have a fact yet — cascade hasn't run"
    )

    # Run the cascade — the missing piece on the monolith path before
    # this fix.
    from concept_model.cascade import recompute_after_turn

    recompute_after_turn(str(db), 101)

    conn = sqlite3.connect(str(db))
    try:
        after = {
            r[0]: r[1] for r in conn.execute(
                "SELECT concept_uuid, value FROM run_concept_facts "
                "WHERE run_id = ?",
                (101,),
            ).fetchall()
        }
    finally:
        conn.close()
    assert after.get(uuids["parent"]) == 300.0, (
        "COMPUTED parent fact did not populate after recompute_after_turn — "
        "the monolith Values page would show leaves only with totals blank "
        f"(facts after cascade: {after})"
    )
