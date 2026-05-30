"""Phase 7 — notes unified into the canonical fact store.

The shared facts API (POST /api/runs/{id}/facts) now branches scalar
vs HTML: a write carrying `html` is sanitised, 30k-capped and routed to
notes_cells with a deterministic concept_uuid; a scalar write keeps
going to run_concept_facts. Existing gotcha #16 invariants (cap,
sanitisation, heading-tag whitelist) are preserved through the new
branch.

| Step | Covered by |
|------|------------|
| 7.2  | test_notes_write_routes_to_notes_cells / test_scalar_write_still_works / test_notes_write_requires_sheet_row_label |
| 7.3  | test_notes_write_over_cap_returns_413 |
| 7.4  | test_notes_write_is_sanitised |
| 7.5  | test_heading_tag_and_in_prose_label_preserved |
| 7.7  | test_notes_concept_uuid_is_deterministic / test_fan_out_rows_get_distinct_uuids |
| 7.8  | test_phase7_e2e_face_and_notes_one_run |
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
FACE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template, import_company_targets
    init_db(db)
    tree = parse_template(str(FACE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    _ct_tid = import_template(db, jp)
    import_company_targets(db, _ct_tid)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('Z', 'n.pdf', 'running', 'Z')"
        )
        run_id = cur.lastrowid
        leaf = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-CuNonCu' AND kind = 'LEAF' LIMIT 1"
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    tc = TestClient(srv.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.leaf_uuid = leaf  # type: ignore[attr-defined]
    tc.db_path = db  # type: ignore[attr-defined]
    return tc


def _notes_rows(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM notes_cells").fetchall()
    finally:
        conn.close()


# -- 7.2 — scalar vs HTML branch --------------------------------------


def test_notes_write_routes_to_notes_cells(client: TestClient) -> None:
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>Hello</p>", "sheet": "Notes-CI", "row": 4,
              "label": "Corporate information"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "NOTE"
    assert body["concept_uuid"]
    rows = _notes_rows(client.db_path)
    assert len(rows) == 1
    assert rows[0]["sheet"] == "Notes-CI" and rows[0]["row"] == 4
    assert rows[0]["concept_uuid"] == body["concept_uuid"]
    assert "<p>Hello</p>" in rows[0]["html"]


def test_scalar_write_still_works(client: TestClient) -> None:
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"concept_uuid": client.leaf_uuid, "value": 12.0,
              "value_status": "observed"},
    )
    assert r.status_code == 200, r.text
    # Scalar write lands in run_concept_facts, NOT notes_cells.
    assert _notes_rows(client.db_path) == []
    conn = sqlite3.connect(str(client.db_path))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE concept_uuid = ?",
            (client.leaf_uuid,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_notes_write_requires_sheet_row_label(client: TestClient) -> None:
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>x</p>", "row": 4, "label": "L"},  # no sheet
    )
    assert r.status_code == 400
    assert "sheet" in r.json()["detail"].lower()


# -- 7.3 — 30k rendered-char cap preserved ----------------------------


def test_notes_write_over_cap_returns_413(client: TestClient) -> None:
    big = "<p>" + ("x" * 30_001) + "</p>"
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": big, "sheet": "Notes-CI", "row": 5, "label": "L"},
    )
    assert r.status_code == 413


# -- 7.4 — sanitisation preserved -------------------------------------


def test_notes_write_is_sanitised(client: TestClient) -> None:
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>safe</p><script>alert(1)</script>",
              "sheet": "Notes-CI", "row": 6, "label": "L"},
    )
    assert r.status_code == 200
    stored = _notes_rows(client.db_path)[0]["html"]
    assert "<script>" not in stored
    assert "safe" in stored


# -- 7.5 — heading tag + in-prose label preserved (gotcha #16) --------


def test_heading_tag_and_in_prose_label_preserved() -> None:
    from notes.html_sanitize import ALLOWED_TAGS, sanitize_notes_html

    # Writer-injected <h3> headings must survive sanitisation.
    assert "h3" in ALLOWED_TAGS
    cleaned, _ = sanitize_notes_html(
        "<h3>2 Revenue</h3><p><strong>(a) Short term benefits</strong></p>"
    )
    assert "<h3>" in cleaned
    # In-prose (a)/(b) sub-section labels stay verbatim in the body.
    assert "(a) Short term benefits" in cleaned
    assert "<strong>" in cleaned


# -- 7.7 — deterministic concept UUIDs for notes rows -----------------


def test_notes_concept_uuid_is_deterministic() -> None:
    from concept_model.parser import mint_notes_concept_uuid

    a = mint_notes_concept_uuid("Notes-CI", 4, "Corporate information")
    b = mint_notes_concept_uuid("Notes-CI", 4, "Corporate information")
    assert a == b


def test_fan_out_rows_get_distinct_uuids() -> None:
    """Sheet-12 LIST_OF_NOTES fans out into many rows; each gets a stable,
    distinct concept UUID."""
    from concept_model.parser import mint_notes_concept_uuid

    uuids = {
        mint_notes_concept_uuid("Notes-Listofnotes", row, f"Note {row}")
        for row in range(10, 30)
    }
    assert len(uuids) == 20  # all distinct


def test_notes_write_rejects_mismatched_concept_uuid(client: TestClient) -> None:
    """Peer-review: a caller must not be able to attach an arbitrary
    concept_uuid (e.g. a face concept's) to a notes row. A mismatched
    UUID is rejected; omitting it derives the deterministic one."""
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>x</p>", "sheet": "Notes-CI", "row": 7, "label": "L",
              "concept_uuid": client.leaf_uuid},  # a FACE concept uuid
    )
    assert r.status_code == 400
    # No row was written under the smuggled identity.
    assert _notes_rows(client.db_path) == []


def test_notes_write_accepts_matching_concept_uuid(client: TestClient) -> None:
    """Round-trip: supplying the correct deterministic UUID is accepted."""
    from concept_model.parser import mint_notes_concept_uuid

    correct = mint_notes_concept_uuid("Notes-CI", 8, "L")
    r = client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>x</p>", "sheet": "Notes-CI", "row": 8, "label": "L",
              "concept_uuid": correct},
    )
    assert r.status_code == 200
    assert _notes_rows(client.db_path)[0]["concept_uuid"] == correct


def test_listofnotes_rows_persist_distinct_uuids(client: TestClient) -> None:
    """Two LIST_OF_NOTES rows written via the shared API land with
    distinct, deterministic concept UUIDs in notes_cells."""
    from concept_model.parser import mint_notes_concept_uuid

    for row, label in [(20, "Revenue note"), (21, "Tax note")]:
        resp = client.post(
            f"/api/runs/{client.run_id}/facts",
            json={"html": f"<p>{label}</p>", "sheet": "Notes-Listofnotes",
                  "row": row, "label": label},
        )
        assert resp.status_code == 200
    rows = _notes_rows(client.db_path)
    by_uuid = {r["concept_uuid"] for r in rows}
    assert len(by_uuid) == 2
    # Deterministic — matches the standalone mint.
    expected = {
        mint_notes_concept_uuid("Notes-Listofnotes", 20, "Revenue note"),
        mint_notes_concept_uuid("Notes-Listofnotes", 21, "Tax note"),
    }
    assert by_uuid == expected


# -- 7.8 — E2E: face + notes in one run via the shared API ------------


def test_phase7_e2e_face_and_notes_one_run(client: TestClient, tmp_path: Path) -> None:
    import shutil
    import openpyxl
    from concept_model.exporter import export_run_to_xlsx
    from db.repository import list_notes_cells_for_run

    # 1) Face scalar fact via the shared API.
    assert client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"concept_uuid": client.leaf_uuid, "value": 500.0,
              "value_status": "observed", "source": "pdf p.2"},
    ).status_code == 200

    # 2) Notes HTML fact via the same endpoint.
    assert client.post(
        f"/api/runs/{client.run_id}/facts",
        json={"html": "<p>Acme Bhd is incorporated in Malaysia.</p>",
              "sheet": "Notes-CI", "row": 4, "label": "Corporate information"},
    ).status_code == 200

    # 3) Face export reads run_concept_facts → value lands on the sheet.
    work = tmp_path / "filled.xlsx"
    shutil.copyfile(FACE, work)
    export_run_to_xlsx(client.db_path, client.run_id, str(work),
                       filing_level="company")
    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    try:
        node = conn.execute(
            "SELECT render_sheet, render_row FROM concept_nodes "
            "WHERE concept_uuid = ?", (client.leaf_uuid,)
        ).fetchone()
        notes = list_notes_cells_for_run(conn, client.run_id)
    finally:
        conn.close()
    ws = openpyxl.load_workbook(str(work), data_only=False)[node["render_sheet"]]
    assert ws[f"B{node['render_row']}"].value == 500.0

    # 4) Notes store carries the HTML row, linked by concept_uuid.
    assert len(notes) == 1
    assert notes[0].sheet == "Notes-CI"
    assert "Acme Bhd" in notes[0].html
