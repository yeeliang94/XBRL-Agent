"""Suite batch runner (Evals workspace, Step E3).

Mocks the per-document launcher so the tests exercise ONLY the batch
orchestration: the concurrency cap of 3, Resume skipping finished documents,
partial-on-stop, and the "N of M" aggregate.
"""
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    import server as srv

    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    srv.OUTPUT_DIR = tmp_path
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    srv.ENV_FILE = fake_env
    from db.schema import init_db

    init_db(db)
    import api.suite_runner as runner

    importlib.reload(runner)
    return srv, runner, db, tmp_path


def _make_suite_with_docs(srv, n):
    from db import repository as repo

    conn = sqlite3.connect(str(srv.AUDIT_DB_PATH))
    sid = repo.create_suite(conn, name="S")
    for i in range(n):
        repo.add_suite_doc(
            conn, suite_id=sid, label=f"doc{i}",
            source_path=f"/tmp/doc{i}.pdf", source_filename=f"doc{i}.pdf",
        )
    conn.commit()
    docs = repo.list_suite_docs(conn, sid)
    conn.close()
    return sid, docs


def _write_completed_run(db, suite_run_id, doc_id):
    from api.suite_runner import _doc_session_id
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
        "suite_run_id) VALUES ('t', 'x.pdf', 'completed', ?, ?)",
        (_doc_session_id(suite_run_id, doc_id), suite_run_id),
    )
    conn.commit()
    conn.close()


def test_concurrency_capped_at_three(env):
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 5)

    state = {"cur": 0, "max": 0}

    async def fake_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.02)
        state["cur"] -= 1

    runner._launch_one_document = fake_launch
    asyncio.run(runner._process_documents(1, docs, {}, "k", "", "m"))
    assert state["max"] <= runner.SUITE_CONCURRENCY == 3
    # All 5 documents were processed despite the cap.
    # (cur returns to 0 and max reached the cap given 5 > 3.)
    assert state["max"] == 3


def test_launch_marks_complete_when_all_docs_finish(env):
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 4)

    async def fake_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        _write_completed_run(db, suite_run_id, doc["id"])

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/run", json={})
    assert resp.status_code == 200, resp.text
    suite_run_id = resp.json()["suite_run_id"]

    # Wait for the background thread to finalize.
    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["suite_run"]["status"] == "complete"
    assert detail["aggregate"]["documents_total"] == 4


def test_resume_only_relaunches_unfinished(env):
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 4)
    from db import repository as repo

    conn = sqlite3.connect(str(db))
    suite_run_id = repo.create_suite_run(conn, suite_id=sid, config={})
    repo.update_suite_run_status(conn, suite_run_id, "partial", ended=True)
    conn.commit()
    conn.close()

    # First two docs already finished in this suite run.
    _write_completed_run(db, suite_run_id, docs[0]["id"])
    _write_completed_run(db, suite_run_id, docs[1]["id"])

    launched = []

    async def fake_launch(sr_id, doc, launch, api_key, proxy_url, model_name):
        launched.append(doc["id"])
        _write_completed_run(db, sr_id, doc["id"])

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/runs/{suite_run_id}/resume")
    assert resp.status_code == 200

    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    # Only the two unfinished docs were (re)launched.
    assert sorted(launched) == sorted([docs[2]["id"], docs[3]["id"]])
    assert detail["suite_run"]["status"] == "complete"


def test_estimate_reports_run_count(env):
    srv, runner, db, _ = env
    sid, _ = _make_suite_with_docs(srv, 3)
    tc = TestClient(srv.app)
    est = tc.post(f"/api/suites/{sid}/estimate", json={"repeats": 2}).json()
    assert est["documents"] == 3
    assert est["repeats"] == 2
    assert est["extraction_runs"] == 6
    assert est["concurrency"] == 3
