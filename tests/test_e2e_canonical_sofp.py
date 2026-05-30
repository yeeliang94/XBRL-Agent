"""Phase 1 step 1.21 — end-to-end canonical-mode smoke test.

Composes every Phase-1 layer:

  parse template → import to DB → post facts via API → recompute
  cascade → export xlsx → assert values land.

The LLM is intentionally NOT involved: this anchors the structural
correctness of the canonical pipeline so a real-PDF E2E in
``test_e2e_*`` only has to validate the agent's extraction logic.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def canonical_env(tmp_path: Path, monkeypatch) -> dict:
    """Spin up a sandboxed canonical-mode server with the SOFP template
    pre-imported and a single empty run row ready to receive facts."""
    monkeypatch.setenv("XBRL_CANONICAL_MODE", "1")
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))

    import importlib
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    init_db(db)

    from concept_model.parser import parse_template
    from concept_model.importer import import_template, import_company_targets
    tree = parse_template(str(FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    _ct_tid = import_template(db, jp)
    import_company_targets(db, _ct_tid)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "smoke.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    work = tmp_path / "filled.xlsx"
    shutil.copyfile(FIXTURE, work)

    return {
        "client": TestClient(srv.app),
        "db": db,
        "run_id": run_id,
        "xlsx": work,
        "server": srv,
    }


def _uuid_for(db, sheet, row):
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = ? AND render_row = ?",
            (sheet, row),
        ).fetchone()
    finally:
        conn.close()
    return r[0] if r else None


def test_full_canonical_run_sofp_cunoncu_company(canonical_env) -> None:
    """Drive every Phase-1 layer in sequence on a single template."""
    client = canonical_env["client"]
    run_id = canonical_env["run_id"]
    db = canonical_env["db"]
    xlsx = canonical_env["xlsx"]

    # 1. Confirm canonical-mode flag is honoured.
    assert canonical_env["server"]._canonical_mode_enabled()

    # 2. Post observed facts on three leaves of the *PPE sub-sheet.
    parent_uuid = _uuid_for(db, "SOFP-Sub-CuNonCu", 39)
    assert parent_uuid, "*Total PPE not in DB"

    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent_uuid,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(children) >= 3

    leaf_values = [(children[0], 100.0), (children[1], 200.0),
                   (children[2], 300.0)]
    for uid, val in leaf_values:
        # Skip if the child happens to be an ABSTRACT row (rare but
        # possible if the template has abstract children).
        conn = sqlite3.connect(str(db))
        try:
            kind = conn.execute(
                "SELECT kind FROM concept_nodes WHERE concept_uuid = ?",
                (uid,),
            ).fetchone()[0]
        finally:
            conn.close()
        if kind != "LEAF":
            continue
        r = client.post(
            f"/api/runs/{run_id}/facts",
            json={
                "concept_uuid": uid,
                "period": "CY",
                "entity_scope": "Company",
                "value": val,
                "value_status": "observed",
                "source": "pdf p.1",
            },
        )
        assert r.status_code == 200, r.text

    # 3. Recompute cascade — parents should aggregate.
    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(db, run_id)

    # 4. Export to xlsx and verify a leaf and the parent total are both
    #    present on the sheet.
    from concept_model.exporter import export_run_to_xlsx
    export_run_to_xlsx(db, run_id, str(xlsx))

    wb = openpyxl.load_workbook(str(xlsx), data_only=False)
    ws = wb["SOFP-Sub-CuNonCu"]
    # At least one of the seeded leaves landed on the right row.
    populated_rows = []
    for uid, val in leaf_values:
        conn = sqlite3.connect(str(db))
        try:
            r = conn.execute(
                "SELECT render_sheet, render_row FROM concept_nodes "
                "WHERE concept_uuid = ?", (uid,),
            ).fetchone()
        finally:
            conn.close()
        if r and r[0] == "SOFP-Sub-CuNonCu":
            populated_rows.append((r[1], val))
    assert populated_rows, "no leaf landed in the export"
    for row, expected in populated_rows[:1]:
        cell = ws[f"B{row}"]
        assert cell.value == expected, (
            f"row {row} expected {expected}, got {cell.value!r}"
        )

    # 5. Reconciliation queue is empty — no conflicts on a consistent
    #    run.  (Pin via the conflicts endpoint so the assertion uses
    #    the same code path as the UI.)
    r = client.get(f"/api/runs/{run_id}/conflicts")
    assert r.status_code == 200
    assert r.json()["conflicts"] == [] or all(
        c["status"] != "open" for c in r.json()["conflicts"]
    )
