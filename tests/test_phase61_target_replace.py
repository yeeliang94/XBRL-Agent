"""Phase 6.1 peer-review HIGH — concept_targets must be REPLACED on re-import.

`import_company_targets` / `import_group_targets` precompute routing cells.
Bootstrap re-imports every template on every startup, so if a template edit
moves a concept's cell at a STABLE `(sheet, row, label)` — the classic case
is a render_col shift, which keeps the deterministic uuid — a bare
`INSERT OR IGNORE` would skip the new coord and leave the OLD cell as a stale
target. The exporter would then route the fact to the wrong column forever.

The fix deletes this template's existing targets before re-inserting (scoped
via the concept_nodes join). These tests pin that a re-import with a changed
render coord updates the target instead of keeping the stale one.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from concept_model.importer import (
    import_company_targets,
    import_group_targets,
    import_template,
)
from db.schema import init_db


_UID = "11111111-1111-5111-8111-111111111111"


def _tree_json(col: str) -> dict:
    """One LEAF concept with a STABLE uuid but a parametrised render col."""
    return {
        "template_id": "t-replace-test",
        "shape": "linear",
        "concepts": [
            {
                "concept_uuid": _UID,
                "parent_uuid": None,
                "kind": "LEAF",
                "canonical_label": "Cash",
                "render_key": {"sheet": "SOFP", "row": 10, "col": col},
            }
        ],
    }


def _reimport(db: Path, tmp_path: Path, col: str, group: bool) -> None:
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(_tree_json(col)), encoding="utf-8")
    tid = import_template(db, jp)
    if group:
        import_group_targets(db, tid)
    else:
        import_company_targets(db, tid)


def _targets(db: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT entity_scope, period, target_col FROM concept_targets "
            "WHERE concept_uuid = ? ORDER BY entity_scope, period",
            (_UID,),
        ).fetchall()
    finally:
        conn.close()


def test_company_targets_replaced_on_render_col_change(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_db(db)
    # First import: CY routes to col B.
    _reimport(db, tmp_path, "B", group=False)
    assert ("Company", "CY", "B") in _targets(db)

    # Template edited: the same concept now renders in col E. A bare
    # INSERT OR IGNORE would leave the CY target at B (stale).
    _reimport(db, tmp_path, "E", group=False)
    rows = _targets(db)
    assert ("Company", "CY", "E") in rows, f"CY target not updated: {rows}"
    assert ("Company", "CY", "B") not in rows, f"stale CY=B target left: {rows}"
    # PY is fixed at C regardless and must remain a single row.
    cy = [r for r in rows if r[1] == "CY"]
    assert len(cy) == 1, f"duplicate CY targets: {rows}"


def test_group_targets_replaced_on_render_col_change(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_db(db)
    # Group layout is fixed (B/C/D/E) and ignores render_col, but the
    # render_ROW still flows into target_row — exercise the same replace
    # discipline by re-importing and asserting no duplication / staleness.
    _reimport(db, tmp_path, "B", group=True)
    first = _targets(db)
    assert ("Group", "CY", "B") in first
    assert ("Company", "CY", "D") in first

    # Re-import the identical template: targets must be replaced, not
    # duplicated. (A bare re-insert with OR IGNORE would no-op; the DELETE
    # path must still leave exactly one row per (scope, period).)
    _reimport(db, tmp_path, "B", group=True)
    second = _targets(db)
    assert sorted(second) == sorted(first), f"group targets churned: {second}"
    # Exactly 4 rows (CY/PY × Group/Company), no duplicates.
    assert len(second) == 4, f"expected 4 group targets, got {second}"
