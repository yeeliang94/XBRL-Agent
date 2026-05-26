"""Phase A1 — in-process fact-write core (`apply_fact` / `write_fact`).

The POST /api/runs/{id}/facts logic used to live entirely inside the route
handler. Phase A of the canonical-live-UI wiring needs the same logic callable
in-process (from the extraction reroute and the canonical correction agent)
without an HTTP round-trip. These tests pin that the extracted core behaves
identically to the route: a valid LEAF write lands in run_concept_facts with an
audit event, and an unknown concept_uuid raises an HTTPException(400).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def db_and_run(tmp_path: Path):
    """A fresh audit DB with the SOFP template imported and one run row."""
    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)

    tree = parse_template(str(FIXTURE))
    json_path = tmp_path / "tree.json"
    json_path.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db_path, json_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
        )
        run_id = cur.lastrowid
        leaf_uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-CuNonCu' AND render_row = 10"
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return db_path, run_id, leaf_uuid


def test_write_fact_persists_leaf_and_audit_event(db_and_run):
    db_path, run_id, leaf_uuid = db_and_run
    from concept_model.facts_api import FactWrite, write_fact

    result = write_fact(
        db_path,
        run_id,
        FactWrite(concept_uuid=leaf_uuid, value=1234.0, value_status="observed"),
    )
    assert result["ok"] is True
    assert result["value"] == 1234.0

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value, value_status FROM run_concept_facts "
            "WHERE run_id = ? AND concept_uuid = ? AND period = 'CY' "
            "AND entity_scope = 'Company'",
            (run_id, leaf_uuid),
        ).fetchone()
        assert row == (1234.0, "observed")
        events = conn.execute(
            "SELECT COUNT(*) FROM concept_fact_events "
            "WHERE run_id = ? AND concept_uuid = ?",
            (run_id, leaf_uuid),
        ).fetchone()[0]
        assert events == 1
    finally:
        conn.close()


def test_write_fact_unknown_concept_raises_400(db_and_run):
    db_path, run_id, _ = db_and_run
    from concept_model.facts_api import FactWrite, write_fact

    with pytest.raises(HTTPException) as exc:
        write_fact(
            db_path,
            run_id,
            FactWrite(concept_uuid="not-a-real-uuid", value=1.0),
        )
    assert exc.value.status_code == 400


def test_apply_fact_reuses_caller_connection(db_and_run):
    """apply_fact takes an open conn so callers can batch writes."""
    db_path, run_id, leaf_uuid = db_and_run
    from concept_model.facts_api import FactWrite, apply_fact, _open_conn

    conn = _open_conn(str(db_path))
    try:
        apply_fact(conn, run_id, FactWrite(concept_uuid=leaf_uuid, value=42.0))
    finally:
        conn.close()

    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id = ? "
            "AND concept_uuid = ?",
            (run_id, leaf_uuid),
        ).fetchone()[0]
        assert val == 42.0
    finally:
        conn.close()
