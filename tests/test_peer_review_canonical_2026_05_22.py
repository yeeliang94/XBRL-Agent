"""Peer-review fixes (2026-05-22) for the canonical concept model.

Pins three correctness gaps a second reviewer flagged, using a synthetic
concept tree so the assertions don't depend on live-template geometry:

1. ``_detect_parent_child_conflicts`` walks the FULL subtree, not just
   direct children — an ``aggregate_only`` parent with observed
   *grandchildren* (under an as-yet-uncomputed subtotal) still raises a
   reconciliation conflict.
2. ``cascade.recompute_after_turn`` recomputes formula MATRIX_CELL totals
   (SOCIE), not only ``kind='COMPUTED'`` rows.
3. The facts-API formula guard refuses observed writes to a formula
   MATRIX_CELL (one with edges) the same way it does for COMPUTED, while
   still accepting writes to a data-entry MATRIX_CELL (no edges).
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _node(conn, *, uuid, template_id, kind, label, sheet, row, col,
          parent=None, matrix_col=None):
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, parent_uuid, "
        "kind, canonical_label, render_sheet, render_row, render_col, "
        "matrix_col) VALUES (?,?,?,?,?,?,?,?,?)",
        (uuid, template_id, parent, kind, label, sheet, row, col, matrix_col),
    )


def _edge(conn, parent, child, coef=1.0):
    conn.execute(
        "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
        "VALUES (?,?,?)",
        (parent, child, coef),
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Server bound to an isolated DB seeded with a synthetic tree:

        P  (COMPUTED)  ─edge→  C (COMPUTED)  ─edge→  G (LEAF)
        M  (MATRIX_CELL, formula) ─edge→ D (MATRIX_CELL, data cell)
    """
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import server as server_module
    importlib.reload(server_module)
    server_module.AUDIT_DB_PATH = db_path

    from db.schema import init_db
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, "
            "imported_at, shape) VALUES ('T', 'x', '2026-05-22T00:00:00Z', "
            "'matrix')"
        )
        # Linear nested hierarchy P → C → G.
        _node(conn, uuid="P", template_id="T", kind="COMPUTED",
              label="Total", sheet="S", row=1, col="B")
        _node(conn, uuid="C", template_id="T", kind="COMPUTED",
              label="Subtotal", sheet="S", row=2, col="B", parent="P")
        _node(conn, uuid="G", template_id="T", kind="LEAF",
              label="Leaf", sheet="S", row=3, col="B", parent="C")
        _edge(conn, "P", "C")
        _edge(conn, "C", "G")
        # Matrix total M (formula) and data cell D (no formula).
        _node(conn, uuid="M", template_id="T", kind="MATRIX_CELL",
              label="Equity at end", sheet="SOCIE", row=10, col="B",
              matrix_col="B")
        _node(conn, uuid="D", template_id="T", kind="MATRIX_CELL",
              label="Profit for the year", sheet="SOCIE", row=11, col="B",
              matrix_col="B")
        _edge(conn, "M", "D")
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('2026-05-22T00:00:00Z', 'x.pdf', 'running', "
            "'2026-05-22T00:00:00Z')"
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(server_module.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.db_path = db_path  # type: ignore[attr-defined]
    return tc


def _post(client, uuid, **fields):
    body = {"concept_uuid": uuid, "period": "CY", "entity_scope": "Company",
            "value": fields.get("value", 100.0),
            "value_status": fields.get("value_status", "observed"),
            "children_status": fields.get("children_status"),
            "source": "pdf p.1"}
    return client.post(f"/api/runs/{client.run_id}/facts", json=body)


def _open_conflicts(db_path, concept_uuid):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM run_concept_conflicts WHERE concept_uuid = ? "
            "AND status = 'open'",
            (concept_uuid,),
        ).fetchone()[0]
    finally:
        conn.close()


def test_aggregate_only_flags_observed_grandchild(client: TestClient) -> None:
    """Finding 1: a deep leaf is observed, then the top parent is marked
    aggregate_only — the intermediate subtotal C has NO fact yet, so a
    direct-child-only check would miss the disagreement."""
    assert _post(client, "G", value=50.0).status_code == 200, "leaf write"
    # Top parent aggregate_only; only the grandchild (G) is observed.
    r = _post(client, "P", value=999.0, children_status="aggregate_only")
    assert r.status_code == 200, r.text
    assert _open_conflicts(client.db_path, "P") == 1, (
        "aggregate_only parent with an observed grandchild must raise a "
        "parent_child_disagree conflict"
    )


def test_observed_write_to_formula_matrix_cell_refused(
    client: TestClient,
) -> None:
    """Finding 3: M is a MATRIX_CELL WITH edges (a SOCIE total) — an
    observed literal without aggregate_only must be refused."""
    r = _post(client, "M", value=100.0, value_status="observed")
    assert r.status_code == 400, r.text
    assert "formula" in r.json()["detail"].lower()


def test_observed_write_to_data_matrix_cell_accepted(
    client: TestClient,
) -> None:
    """Finding 3 (negative): D is a MATRIX_CELL with NO edges — a genuine
    component-movement data cell — and must still accept observed writes."""
    r = _post(client, "D", value=100.0, value_status="observed")
    assert r.status_code == 200, r.text


def test_aggregate_only_accepted_on_formula_matrix_cell(
    client: TestClient,
) -> None:
    """Finding 3 (escape hatch): aggregate_only on a formula matrix cell is
    accepted and flips to user_override, same as for COMPUTED rows."""
    r = _post(client, "M", value=100.0, children_status="aggregate_only")
    assert r.status_code == 200, r.text
    assert r.json()["value_status"] == "user_override"


def test_cascade_recomputes_formula_matrix_total(client: TestClient) -> None:
    """Finding 2: posting only the component data cell D and running the
    cascade must populate the formula matrix total M (=D)."""
    assert _post(client, "D", value=42.0).status_code == 200
    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(client.db_path, client.run_id)

    conn = sqlite3.connect(str(client.db_path))
    try:
        row = conn.execute(
            "SELECT value, value_status FROM run_concept_facts "
            "WHERE run_id = ? AND concept_uuid = 'M' AND period = 'CY' "
            "AND entity_scope = 'Company'",
            (client.run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "matrix total M was never recomputed"
    assert row[0] == 42.0
    assert row[1] == "observed"
