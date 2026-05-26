"""Phase 1 steps 1.18-1.20 — concepts page + reconciliation queue endpoints."""
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
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    init_db(db)
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    tree = parse_template(str(FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db, jp)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "x.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        # Seed a single fact + a conflict for the endpoints to surface.
        leaf_uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-CuNonCu' AND render_row = 10"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'pdf', ?)",
            (run_id, leaf_uuid, 42.0, "2026-05-21Z"),
        )
        conn.execute(
            "INSERT INTO run_concept_conflicts(run_id, concept_uuid, "
            "period, entity_scope, kind, residual, detail, status, "
            "created_at) VALUES (?, ?, 'CY', 'Company', "
            "'partial_state', 3000.0, 'demo', 'open', ?)",
            (run_id, leaf_uuid, "2026-05-21Z"),
        )
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(srv.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.leaf_uuid = leaf_uuid  # type: ignore[attr-defined]
    tc.db_path = db  # type: ignore[attr-defined]
    return tc


def test_get_concepts_returns_tree_with_facts(client: TestClient) -> None:
    r = client.get(f"/api/runs/{client.run_id}/concepts")
    assert r.status_code == 200
    payload = r.json()
    assert payload["run_id"] == client.run_id
    concepts = payload["concepts"]
    assert len(concepts) > 10  # SOFP has dozens of concepts
    # The seeded leaf carries its value.
    leaf = next(c for c in concepts if c["concept_uuid"] == client.leaf_uuid)
    assert leaf["value"] == 42.0


def test_list_templates_and_template_concepts_run_independent(
    client: TestClient,
) -> None:
    """Phase 5.1: the global settings endpoints expose templates + their
    concept vocabulary WITHOUT any run scope or values."""
    tpls = client.get("/api/templates").json()["templates"]
    assert len(tpls) >= 1
    tid = tpls[0]["template_id"]

    payload = client.get(f"/api/templates/{tid}/concepts").json()
    assert payload["template_id"] == tid
    concepts = payload["concepts"]
    assert len(concepts) > 10
    # Labels only — no value / value_status keys (those are run-scoped).
    sample = concepts[0]
    assert "canonical_label" in sample and "display_label" in sample
    assert "value" not in sample


def test_template_concepts_unknown_404(client: TestClient) -> None:
    assert client.get("/api/templates/nope/concepts").status_code == 404


def test_aborted_run_is_still_reviewable(client: TestClient) -> None:
    """Phase 4.2: a run that ended early (aborted / partial) must still
    expose whatever facts it managed to write, so the user can review and
    edit the partial result. The concepts endpoint keys on facts, not on
    terminal status."""
    conn = sqlite3.connect(str(client.db_path))
    try:
        conn.execute(
            "UPDATE runs SET status = 'aborted', ended_at = ? WHERE id = ?",
            ("2026-05-21T02:00:00Z", client.run_id),
        )
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"/api/runs/{client.run_id}/concepts")
    assert r.status_code == 200
    leaf = next(
        c for c in r.json()["concepts"]
        if c["concept_uuid"] == client.leaf_uuid
    )
    assert leaf["value"] == 42.0


def test_get_concepts_excludes_templates_not_in_run(
    client: TestClient, tmp_path: Path,
) -> None:
    """Peer-review #3: in a long-lived DB with several templates
    imported, the concepts endpoint must return ONLY the templates the
    run actually touched (inferred from run_concept_facts), not every
    concept_nodes row in the DB.

    We import a SECOND, unrelated template (SOPL-Function) into the
    same DB without writing any facts for it, then assert the run's
    concepts response does not leak it.
    """
    import json as _json
    from concept_model.parser import parse_template
    from concept_model.importer import import_template

    other = REPO / "XBRL-template-MFRS" / "Company" / "03-SOPL-Function.xlsx"
    tree = parse_template(str(other))
    jp = tmp_path / "other.json"
    jp.write_text(_json.dumps(tree.to_json(), sort_keys=True),
                  encoding="utf-8")
    import_template(client.db_path, jp)

    payload = client.get(f"/api/runs/{client.run_id}/concepts").json()
    seen_templates = {c["template_id"] for c in payload["concepts"]}
    # The run only ever wrote facts on the SOFP template.
    assert seen_templates == {"mfrs-company-sofp-cunoncu-v1"}, seen_templates


def test_linear_concepts_carry_matrix_metadata(client: TestClient) -> None:
    """Phase 5 step 5.6 — every concept row now carries `matrix_col`
    (NULL on linear templates) and the template `shape` so the UI can
    decide between the linear tree and the matrix grid."""
    payload = client.get(f"/api/runs/{client.run_id}/concepts").json()
    leaf = next(c for c in payload["concepts"]
                if c["concept_uuid"] == client.leaf_uuid)
    assert "matrix_col" in leaf and leaf["matrix_col"] is None
    assert leaf["shape"] == "linear"


def test_socie_concepts_carry_matrix_shape(tmp_path: Path, monkeypatch) -> None:
    """A SOCIE run's concepts report shape='matrix' with matrix_col set."""
    db = tmp_path / "socie.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    init_db(db)
    socie = REPO / "XBRL-template-MFRS" / "Company" / "09-SOCIE.xlsx"
    tree = parse_template(str(socie))
    jp = tmp_path / "socie.json"
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
            "AND render_row = 11 AND matrix_col = 'C'", (tid,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', 5.0, 'observed', 'Z')",
            (run_id, node),
        )
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(srv.app)
    payload = tc.get(f"/api/runs/{run_id}/concepts").json()
    mx = [c for c in payload["concepts"] if c["kind"] == "MATRIX_CELL"]
    assert mx, "no MATRIX_CELL concepts returned"
    assert all(c["shape"] == "matrix" for c in payload["concepts"])
    assert any(c["matrix_col"] == "C" for c in mx)


def test_patch_display_label(client: TestClient) -> None:
    r = client.patch(
        f"/api/concepts/{client.leaf_uuid}/display_label",
        json={"display_label": "Cows and pigs"},
    )
    assert r.status_code == 200
    # The override now appears in subsequent GETs.
    payload = client.get(f"/api/runs/{client.run_id}/concepts").json()
    leaf = next(c for c in payload["concepts"]
                if c["concept_uuid"] == client.leaf_uuid)
    assert leaf["display_label"] == "Cows and pigs"
    # Canonical stays as-is.
    assert leaf["canonical_label"] != "Cows and pigs"


def test_get_conflicts_lists_open_items(client: TestClient) -> None:
    r = client.get(f"/api/runs/{client.run_id}/conflicts")
    assert r.status_code == 200
    body = r.json()
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["kind"] == "partial_state"
    assert body["conflicts"][0]["residual"] == 3000.0


def test_resolve_conflict(client: TestClient) -> None:
    # Find the conflict id.
    items = client.get(f"/api/runs/{client.run_id}/conflicts").json()
    conflict_id = items["conflicts"][0]["id"]

    r = client.post(f"/api/conflicts/{conflict_id}/resolve",
                    json={"action": "resolved"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_resolve_conflict_unknown_id_returns_404(client: TestClient) -> None:
    r = client.post("/api/conflicts/99999/resolve",
                    json={"action": "resolved"})
    assert r.status_code == 404


def test_resolve_rejects_unknown_action(client: TestClient) -> None:
    items = client.get(f"/api/runs/{client.run_id}/conflicts").json()
    conflict_id = items["conflicts"][0]["id"]
    r = client.post(f"/api/conflicts/{conflict_id}/resolve",
                    json={"action": "ignore"})
    assert r.status_code == 400
