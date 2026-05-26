"""Phase 1.1 + 1.2 — PATCH /api/runs/{id}/facts/{concept_uuid}.

The editable-review plan turns the read-only concepts surface into an
editable one. This covers the backend half:

  1.1  a user value edit lands as value_status='user_override' (or
       not_disclosed when cleared), journalled like any agent write;
  1.1  edits to ABSTRACT (section header) and formula-owning concepts
       (COMPUTED / matrix totals) are refused — the user edits leaves;
  1.2  after a leaf edit the cascade recomputes the dependent subtotals
       and the response carries the recomputed ancestor values.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Boot a server with an isolated audit DB + the SOFP template
    imported, and stash a run row. Mirrors tests/test_facts_api.py."""
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))

    import importlib
    import server as server_module
    importlib.reload(server_module)
    server_module.AUDIT_DB_PATH = db_path

    from db.schema import init_db
    init_db(db_path)

    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    tree = parse_template(str(FIXTURE))
    json_path = tmp_path / "tree.json"
    json_path.write_text(
        json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8"
    )
    import_template(db_path, json_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-25T00:00:00Z", "x.pdf", "running",
             "2026-05-25T00:00:00Z"),
        )
        run_id = cur.lastrowid

        # A formula parent (COMPUTED) whose children are ALL leaves (≥2),
        # so seeding every child gives the cascade a complete breakdown to
        # re-sum after a leaf edit. A parent with intermediate COMPUTED
        # children would need those filled first, which muddies the test.
        parent_uuid = None
        leaf_children: list[str] = []
        for row in conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE kind = 'COMPUTED'"
        ).fetchall():
            cand = row["concept_uuid"]
            kids = conn.execute(
                "SELECT e.child_uuid, n.kind FROM concept_edges e "
                "JOIN concept_nodes n ON n.concept_uuid = e.child_uuid "
                "WHERE e.parent_uuid = ?",
                (cand,),
            ).fetchall()
            if len(kids) >= 2 and all(k["kind"] == "LEAF" for k in kids):
                parent_uuid = cand
                leaf_children = [k["child_uuid"] for k in kids]
                break
        assert parent_uuid is not None, "no COMPUTED row with all-leaf children"

        abstract_uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE kind = 'ABSTRACT' "
            "LIMIT 1"
        ).fetchone()["concept_uuid"]
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(server_module.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.parent_uuid = parent_uuid  # type: ignore[attr-defined]
    tc.leaf_children = leaf_children  # type: ignore[attr-defined]
    tc.abstract_uuid = abstract_uuid  # type: ignore[attr-defined]
    tc.db_path = db_path  # type: ignore[attr-defined]
    return tc


def _fact(client: TestClient, concept_uuid: str):
    conn = sqlite3.connect(str(client.db_path))
    try:
        return conn.execute(
            "SELECT value, value_status FROM run_concept_facts "
            "WHERE run_id = ? AND concept_uuid = ? AND period = 'CY' "
            "AND entity_scope = 'Company'",
            (client.run_id, concept_uuid),
        ).fetchone()
    finally:
        conn.close()


def test_patch_leaf_value_persists_as_user_override(client: TestClient) -> None:
    leaf = client.leaf_children[0]
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{leaf}", json={"value": 123.0}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == 123.0
    assert body["value_status"] == "user_override"
    row = _fact(client, leaf)
    assert row[0] == 123.0 and row[1] == "user_override"


def test_patch_clear_value_marks_not_disclosed(client: TestClient) -> None:
    leaf = client.leaf_children[0]
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{leaf}", json={"value": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["value_status"] == "not_disclosed"


def test_patch_refuses_abstract(client: TestClient) -> None:
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{client.abstract_uuid}",
        json={"value": 1.0},
    )
    assert r.status_code == 400
    assert "header" in r.json()["detail"].lower()


def test_patch_refuses_formula_concept(client: TestClient) -> None:
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{client.parent_uuid}",
        json={"value": 1.0},
    )
    assert r.status_code == 400
    assert "automatic" in r.json()["detail"].lower() or \
        "computed" in r.json()["detail"].lower()


def test_patch_unknown_concept_404(client: TestClient) -> None:
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/does-not-exist",
        json={"value": 1.0},
    )
    assert r.status_code == 404


def test_patch_typed_zero_is_user_override_not_cleared(client: TestClient) -> None:
    """Phase 4.1: a typed 0 is a deliberate override of value 0, distinct
    from a cleared (null → not_disclosed) cell."""
    leaf = client.leaf_children[0]
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{leaf}", json={"value": 0}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == 0
    assert body["value_status"] == "user_override"


def test_patch_rejects_non_finite(client: TestClient) -> None:
    """Phase 4.1: NaN / Infinity are refused — they'd poison the cascade
    and produce an unopenable Excel cell."""
    import json as _json
    leaf = client.leaf_children[0]
    # httpx won't serialise float('nan'); send the literal JSON token.
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{leaf}",
        content=_json.dumps({"value": float("inf")}).replace("Infinity", "Infinity"),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "finite" in r.json()["detail"].lower()


def test_leaf_edit_recomputes_parent_subtotal(client: TestClient) -> None:
    """The core 1.2 contract: editing a leaf re-sums its parent and the
    response carries the recomputed ancestor value."""
    children = client.leaf_children
    # Seed every child so the parent has a complete breakdown (value 1 each).
    for uuid in children:
        client.post(
            f"/api/runs/{client.run_id}/facts",
            json={"concept_uuid": uuid, "value": 1.0,
                  "value_status": "observed"},
        )
    # Edit the first child to 30; parent total = 30 + (n-1)*1.
    expected = 30.0 + (len(children) - 1) * 1.0
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{children[0]}", json={"value": 30.0}
    )
    assert r.status_code == 200, r.text
    recomputed = {x["concept_uuid"]: x["value"] for x in r.json()["recomputed"]}
    assert client.parent_uuid in recomputed
    assert recomputed[client.parent_uuid] == pytest.approx(expected)
    # And it's durable in the DB, not just the response.
    assert _fact(client, client.parent_uuid)[0] == pytest.approx(expected)


def test_leaf_edit_recomputes_parent_with_blank_siblings(
    client: TestClient,
) -> None:
    """Review edits mirror Excel formulas: blank sibling rows count as zero
    once any child has a numeric value."""
    leaf = client.leaf_children[0]
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{leaf}", json={"value": 30.0}
    )
    assert r.status_code == 200, r.text
    recomputed = {x["concept_uuid"]: x["value"] for x in r.json()["recomputed"]}
    assert client.parent_uuid in recomputed
    assert recomputed[client.parent_uuid] == pytest.approx(30.0)
    assert _fact(client, client.parent_uuid)[0] == pytest.approx(30.0)
