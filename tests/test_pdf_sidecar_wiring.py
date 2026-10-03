"""Retired PDF transcripts remain readable in historical run details."""
import json
import server
from fastapi.testclient import TestClient

client = TestClient(server.app)


def test_outcome_round_trips_through_disk(tmp_path):
    from ingest.pdf_sidecar import (
        SIDECAR_OUTCOME_NAME, read_sidecar_outcome,
    )
    payload = {"status": "skipped", "reason": "too_many_pages",
               "pages_requested": 120, "page_cap": 80}
    (tmp_path / SIDECAR_OUTCOME_NAME).write_text(json.dumps(payload))
    assert (tmp_path / SIDECAR_OUTCOME_NAME).is_file()
    assert read_sidecar_outcome(tmp_path) == payload
    # Absent / unreadable / missing dir → None, never a raise.
    assert read_sidecar_outcome(tmp_path / "nope") is None
    assert read_sidecar_outcome(None) is None
    (tmp_path / SIDECAR_OUTCOME_NAME).write_text("not json", encoding="utf-8")
    assert read_sidecar_outcome(tmp_path) is None


def test_run_detail_returns_persisted_outcome(tmp_path, monkeypatch):
    """GET /api/runs/{id} carries `pdf_sidecar` from the run's output dir, and
    null when no outcome file exists (pre-feature run / pass did not apply)."""
    import sqlite3
    from db.schema import init_db
    from ingest.pdf_sidecar import SIDECAR_OUTCOME_NAME

    db = tmp_path / "audit.db"
    init_db(db)
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs (id, status, created_at, pdf_filename, output_dir) "
            "VALUES (1, 'completed', '2026-08-18T00:00:00', 'scan.pdf', ?)",
            (str(out_dir),),
        )
        conn.execute(
            "INSERT INTO runs (id, status, created_at, pdf_filename, output_dir) "
            "VALUES (2, 'completed', '2026-08-18T00:00:00', 'text.pdf', ?)",
            (str(tmp_path / "other"),),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(server, "_open_audit_conn", lambda: sqlite3.connect(str(db)))

    (out_dir / SIDECAR_OUTCOME_NAME).write_text(json.dumps({
        "status": "built", "pages": 20,
        "usage": {"in": 56760, "out": 13976},
    }))
    detail = client.get("/api/runs/1").json()
    assert detail["pdf_sidecar"] == {"status": "built", "pages": 20,
                                     "usage": {"in": 56760, "out": 13976}}
    assert client.get("/api/runs/2").json()["pdf_sidecar"] is None
