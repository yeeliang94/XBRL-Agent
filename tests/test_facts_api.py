"""Phase 1 steps 1.4 - 1.8 — POST /api/runs/{id}/facts endpoint.

Covers:
  1.4  endpoint scaffolding + 200 echo on stub payload
  1.5  unknown concept_uuid → 400
  1.6  kind-aware validation: observed-on-LEAF=200, observed-on-COMPUTED/
       ABSTRACT=400
  1.7  children_status only allowed on COMPUTED concepts
  1.8  (run_id, concept_uuid, period, entity_scope) composite key, upsert
       writes audit-event rows
"""
from __future__ import annotations

import json
import sqlite3
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Boot a fresh server with an isolated audit DB and a single
    canonical template pre-imported.  Returns a TestClient plus a few
    handles via attributes for the test bodies to drive."""
    # Point the audit DB at the tmp dir BEFORE importing server.py so
    # the lifespan handler picks up the right path.
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))

    # server.py reads OUTPUT_DIR at import time; reimport cleanly.
    import importlib
    import server as server_module
    importlib.reload(server_module)
    # Point AUDIT_DB_PATH explicitly — _open_audit_conn uses it directly.
    server_module.AUDIT_DB_PATH = db_path

    from db.schema import init_db
    init_db(db_path)

    # Parse + import the SOFP template so we have real concepts in DB.
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    tree = parse_template(str(FIXTURE))
    json_path = tmp_path / "tree.json"
    json_path.write_text(
        json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8"
    )
    import_template(db_path, json_path)

    # Stash a run row so /api/runs/{id}/facts has a parent FK.
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "x.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        # Pick known concepts: a LEAF and a COMPUTED and an ABSTRACT
        # from the SOFP face sheet.
        cur = conn.execute(
            "SELECT concept_uuid, kind FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-CuNonCu' AND render_row IN (10, 23, 7)"
        )
        kind_by_row = {
            int(row[0]): row[1]
            for row in conn.execute(
                "SELECT render_row, concept_uuid FROM concept_nodes "
                "WHERE render_sheet = 'SOFP-CuNonCu' "
                "AND render_row IN (7, 10, 23, 44)"
            ).fetchall()
        }
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(server_module.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.leaf_uuid = kind_by_row[10]  # type: ignore[attr-defined]
    tc.abstract_uuid = kind_by_row[7]  # type: ignore[attr-defined]
    tc.computed_uuid = kind_by_row[23]  # type: ignore[attr-defined]
    tc.db_path = db_path  # type: ignore[attr-defined]
    return tc


def _post_fact(client: TestClient, **fields) -> "Response":  # type: ignore
    body = {
        "concept_uuid": fields.get("concept_uuid"),
        "period": fields.get("period", "CY"),
        "entity_scope": fields.get("entity_scope", "Company"),
        "value": fields.get("value", 100.0),
        "value_status": fields.get("value_status", "observed"),
        "children_status": fields.get("children_status"),
        "source": fields.get("source", "pdf p.1"),
        "evidence": fields.get("evidence"),
    }
    return client.post(f"/api/runs/{client.run_id}/facts", json=body)


def test_post_facts_endpoint_exists(client: TestClient) -> None:
    """Step 1.4: stub payload echo returns 200."""
    r = _post_fact(client, concept_uuid=client.leaf_uuid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["concept_uuid"] == client.leaf_uuid


def test_post_facts_rejects_unknown_concept_uuid(client: TestClient) -> None:
    """Step 1.5: garbage UUID → 400 with a structured error."""
    r = _post_fact(client, concept_uuid="00000000-0000-0000-0000-000000000000")
    assert r.status_code == 400
    assert "concept" in r.json()["detail"].lower()


def test_post_facts_rejects_observed_on_computed_concept(
    client: TestClient,
) -> None:
    """Step 1.6: observed value on a COMPUTED row → 400 (cascade owns
    those values, the agent must not bypass it)."""
    r = _post_fact(client, concept_uuid=client.computed_uuid,
                   value_status="observed")
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "computed" in detail or "formula" in detail


def test_post_facts_rejects_observed_on_abstract_concept(
    client: TestClient,
) -> None:
    """Step 1.6: ABSTRACT rows are section headers — never writable
    (DB echo of gotcha #17)."""
    r = _post_fact(client, concept_uuid=client.abstract_uuid,
                   value_status="observed")
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "abstract" in detail or "header" in detail


def test_post_facts_accepts_observed_on_leaf_concept(client: TestClient) -> None:
    """Step 1.6: the happy path — observed value on a LEAF row → 200."""
    r = _post_fact(client, concept_uuid=client.leaf_uuid,
                   value_status="observed", value=42.0)
    assert r.status_code == 200


def test_children_status_only_allowed_on_computed_concept(
    client: TestClient,
) -> None:
    """Step 1.7: children_status is meaningless on a LEAF row.

    children_status describes whether the underlying breakdown was
    itemised or aggregated — a LEAF has no children to describe.
    """
    r = _post_fact(client, concept_uuid=client.leaf_uuid,
                   children_status="aggregate_only")
    assert r.status_code == 400


def test_facts_composite_key_includes_period_and_entity_scope(
    client: TestClient,
) -> None:
    """Step 1.8: CY+Group and CY+Company are two distinct facts; both
    persist independently."""
    r1 = _post_fact(client, concept_uuid=client.leaf_uuid,
                    period="CY", entity_scope="Company", value=100.0)
    r2 = _post_fact(client, concept_uuid=client.leaf_uuid,
                    period="CY", entity_scope="Group", value=200.0)
    assert r1.status_code == r2.status_code == 200

    conn = sqlite3.connect(str(client.db_path))
    try:
        rows = conn.execute(
            "SELECT period, entity_scope, value FROM run_concept_facts "
            "WHERE concept_uuid = ? ORDER BY entity_scope",
            (client.leaf_uuid,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    scopes = {r[1] for r in rows}
    assert scopes == {"Company", "Group"}


def test_leaf_write_after_aggregate_only_parent_creates_conflict(
    client: TestClient,
) -> None:
    """Peer-review #5: if a COMPUTED parent is already marked
    aggregate_only and a child LEAF is written afterwards, the API
    must flag a parent_child_disagree conflict.

    The Phase-1 detection only fired on the aggregate_only WRITE, so
    the later leaf write slipped through silently.
    """
    import sqlite3
    # Find a COMPUTED parent on the sub-sheet + one of its leaf children.
    conn = sqlite3.connect(str(client.db_path))
    try:
        parent = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-Sub-CuNonCu' AND render_row = 39"
        ).fetchone()[0]
        child = conn.execute(
            "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ? "
            "LIMIT 1", (parent,),
        ).fetchone()[0]
    finally:
        conn.close()

    # 1. Mark the parent aggregate_only first.
    r1 = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={
            "concept_uuid": parent, "period": "CY",
            "entity_scope": "Company", "value": 50000.0,
            "value_status": "observed",
            "children_status": "aggregate_only",
            "source": "pdf",
        },
    )
    assert r1.status_code == 200, r1.text

    # 2. Now write a child LEAF — this should raise a conflict because
    # the parent is aggregate_only.
    r2 = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={
            "concept_uuid": child, "period": "CY",
            "entity_scope": "Company", "value": 20000.0,
            "value_status": "observed", "source": "pdf",
        },
    )
    assert r2.status_code == 200, r2.text

    conn = sqlite3.connect(str(client.db_path))
    try:
        conflicts = conn.execute(
            "SELECT kind FROM run_concept_conflicts WHERE run_id = ? "
            "AND concept_uuid = ?",
            (client.run_id, parent),
        ).fetchall()
    finally:
        conn.close()
    assert any(c[0] == "parent_child_disagree" for c in conflicts), conflicts


def test_post_facts_rejects_invalid_period(client: TestClient) -> None:
    """Peer-review #4: period must be CY|PY — a free string like a year
    must be rejected (422) before it can silently default a column."""
    body = {
        "concept_uuid": client.leaf_uuid,
        "period": "2024",
        "entity_scope": "Company",
        "value": 10.0,
        "value_status": "observed",
    }
    r = client.post(f"/api/runs/{client.run_id}/facts", json=body)
    assert r.status_code == 422, r.text


def test_post_facts_rejects_invalid_entity_scope(client: TestClient) -> None:
    """Peer-review #4: entity_scope must be Company|Group."""
    body = {
        "concept_uuid": client.leaf_uuid,
        "period": "CY",
        "entity_scope": "Consolidated",
        "value": 10.0,
        "value_status": "observed",
    }
    r = client.post(f"/api/runs/{client.run_id}/facts", json=body)
    assert r.status_code == 422, r.text


def test_facts_upsert_on_same_composite_key(client: TestClient) -> None:
    """Step 1.8: repeating the same key — latest value wins, but the
    audit log carries both events."""
    _post_fact(client, concept_uuid=client.leaf_uuid, value=100.0)
    _post_fact(client, concept_uuid=client.leaf_uuid, value=200.0)

    conn = sqlite3.connect(str(client.db_path))
    try:
        facts = conn.execute(
            "SELECT value FROM run_concept_facts WHERE concept_uuid = ?",
            (client.leaf_uuid,),
        ).fetchall()
        events = conn.execute(
            "SELECT id FROM concept_fact_events WHERE concept_uuid = ?",
            (client.leaf_uuid,),
        ).fetchall()
    finally:
        conn.close()

    assert len(facts) == 1
    assert facts[0][0] == 200.0
    assert len(events) >= 2, "audit log should record each change"
