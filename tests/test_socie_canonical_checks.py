"""Phase 5 step 5.5 — SOCIE cross-checks branch on matrix dimensions.

The xlsx-based SOCIE cross-checks already pick their read column by
filing standard (gotcha #15): MFRS reads the Total column X (24), MPERS
reads col B (2), and Group filings scope each read to a vertical block
range (cross_checks.util.SOCIE_GROUP_BLOCKS).

These tests pin that a *canonical-mode* SOCIE export lands the
equity-at-end-of-period total in exactly the cell those cross-checks
read — i.e. the matrix parser/importer/exporter chain produces output
that the standard-branching cross-checks can consume without change.
If the matrix column mapping ever drifts, the cross-checks would read a
blank cell and these tests fail loudly.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from concept_model.parser import parse_template
from concept_model.importer import import_template
from concept_model.exporter import export_run_to_xlsx
from cross_checks.util import (
    SOCIE_GROUP_BLOCKS,
    find_value_in_block,
    open_workbook,
    socie_total_column,
)
from db.schema import init_db

_ROOT = Path(__file__).resolve().parent.parent
MFRS_GROUP = _ROOT / "XBRL-template-MFRS" / "Group" / "09-SOCIE.xlsx"
MPERS_GROUP = _ROOT / "XBRL-template-MPERS" / "Group" / "09-SOCIE.xlsx"

_EQUITY_END = "equity at end of period"


def _seed_and_export(db: Path, fixture: Path, tmp_path: Path, *, matrix_col: str,
                     value: float) -> Path:
    tree = parse_template(str(fixture))
    jp = tmp_path / f"{tree.template_id}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-22Z", "s.pdf", "running", "2026-05-22Z"),
        )
        run_id = cur.lastrowid
        node = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
            "AND LOWER(REPLACE(canonical_label,'*','')) LIKE ? AND matrix_col = ?",
            (tid, f"%{_EQUITY_END}%", matrix_col),
        ).fetchone()
        assert node is not None, f"no equity-at-end concept at col {matrix_col}"
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, 'CY', 'Group', ?, 'observed', 'Z')",
            (run_id, node[0], value),
        )
        conn.commit()
    finally:
        conn.close()

    work = tmp_path / "filled.xlsx"
    shutil.copyfile(fixture, work)
    export_run_to_xlsx(db, run_id, str(work), filing_level="group")
    return work


def test_mfrs_group_equity_total_lands_in_cross_check_column(tmp_path: Path) -> None:
    if not MFRS_GROUP.exists():
        pytest.skip("fixture missing")
    db = tmp_path / "x.db"
    init_db(db)
    # MFRS total column is X (24).
    work = _seed_and_export(db, MFRS_GROUP, tmp_path, matrix_col="X", value=12345.0)

    wb = open_workbook(str(work))
    ws = wb["SOCIE"]
    col = socie_total_column("mfrs")  # 24
    start, end = SOCIE_GROUP_BLOCKS["group_cy"]
    got = find_value_in_block(ws, _EQUITY_END, col, start, end, wb=wb)
    assert got == 12345.0


def test_mpers_group_equity_total_lands_in_cross_check_column(tmp_path: Path) -> None:
    if not MPERS_GROUP.exists():
        pytest.skip("fixture missing")
    db = tmp_path / "x.db"
    init_db(db)
    # MPERS total column is B (2).
    work = _seed_and_export(db, MPERS_GROUP, tmp_path, matrix_col="B", value=67890.0)

    wb = open_workbook(str(work))
    ws = wb["SOCIE"]
    col = socie_total_column("mpers")  # 2
    start, end = SOCIE_GROUP_BLOCKS["group_cy"]
    got = find_value_in_block(ws, _EQUITY_END, col, start, end, wb=wb)
    assert got == 67890.0
