"""Per-run notes-table style override endpoint + repository (PLAN-notes-table-theme).

PATCH /api/runs/{id}/notes_table_style must work on ANY run status (review is
post-run), persist via the v22 column, surface on the run-detail GET, validate
bad payloads (400), and clear on null.
"""
import sqlite3

import server
from fastapi.testclient import TestClient
from server import app
from db import repository as repo
from db.schema import init_db

client = TestClient(app)


def _make_run(db_path, status="completed_with_errors") -> int:
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO runs (id, status, created_at, pdf_filename) "
            "VALUES (1, ?, '2026-06-23T00:00:00', 'x.pdf')",
            (status,),
        )
        conn.commit()
    finally:
        conn.close()
    return 1


def test_set_and_read_run_notes_table_style(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))
    run_id = _make_run(db)

    # Works on a COMPLETED run (not draft) — the key requirement.
    resp = client.patch(
        f"/api/runs/{run_id}/notes_table_style",
        json={"notes_table_style": {"borderColor": "#185FA5", "borderStyle": "single"}},
    )
    assert resp.status_code == 200
    assert resp.json()["notes_table_style"]["borderColor"] == "#185fa5"

    # Surfaces on the run-detail GET so the Notes tab can seed the override.
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["notes_table_style"]["borderColor"] == "#185fa5"

    # Persisted via the repository helper too.
    conn = sqlite3.connect(str(db))
    try:
        run = repo.fetch_run(conn, run_id)
        assert run.notes_table_style["borderColor"] == "#185fa5"
    finally:
        conn.close()


def test_clear_run_notes_table_style(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))
    run_id = _make_run(db)
    client.patch(
        f"/api/runs/{run_id}/notes_table_style",
        json={"notes_table_style": {"borderColor": "#000000"}},
    )
    # null clears → inherit firm default (None).
    resp = client.patch(
        f"/api/runs/{run_id}/notes_table_style",
        json={"notes_table_style": None},
    )
    assert resp.status_code == 200
    assert resp.json()["notes_table_style"] is None
    assert client.get(f"/api/runs/{run_id}").json()["notes_table_style"] is None


def test_run_notes_table_style_rejects_malformed(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))
    run_id = _make_run(db)
    resp = client.patch(
        f"/api/runs/{run_id}/notes_table_style",
        json={"notes_table_style": {"borderColor": "url(x)"}},
    )
    assert resp.status_code == 400


def test_falsy_malformed_payloads_400_not_clear(tmp_path, monkeypatch):
    """Only null / {} clear the override; falsy-but-malformed values
    (false / "" / []) must still 400 (peer-review LOW #6)."""
    db = tmp_path / "audit.db"
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))
    run_id = _make_run(db)
    for bad in (False, "", []):
        resp = client.patch(
            f"/api/runs/{run_id}/notes_table_style",
            json={"notes_table_style": bad},
        )
        assert resp.status_code == 400, bad
    # …while null and {} legitimately clear.
    for clear in (None, {}):
        resp = client.patch(
            f"/api/runs/{run_id}/notes_table_style",
            json={"notes_table_style": clear},
        )
        assert resp.status_code == 200
        assert resp.json()["notes_table_style"] is None


def test_run_notes_table_style_404_on_missing_run(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    init_db(db)
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))
    resp = client.patch(
        "/api/runs/999/notes_table_style",
        json={"notes_table_style": {"borderColor": "#000000"}},
    )
    assert resp.status_code == 404
