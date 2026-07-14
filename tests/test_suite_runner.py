"""Suite batch runner (Evals workspace, Step E3).

Mocks the per-document launcher so the tests exercise ONLY the batch
orchestration: the concurrency cap of 3, Resume skipping finished documents,
partial-on-stop, and the "N of M" aggregate.
"""
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import threading
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


def _write_n_completed_runs(db, suite_run_id, doc_id, n):
    for _ in range(n):
        _write_completed_run(db, suite_run_id, doc_id)


def test_partial_repeats_keep_suite_partial(env):
    """PLAN-evals-hardening Step 1: a document asking for 2 repeats with only
    1 finished is NOT a finished document — the suite must land 'partial',
    not 'complete'."""
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 2)

    async def fake_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        # Every doc finishes only ONE of its two requested repeats.
        _write_completed_run(db, suite_run_id, doc["id"])

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/run", json={"repeats": 2})
    assert resp.status_code == 200, resp.text
    suite_run_id = resp.json()["suite_run_id"]

    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["suite_run"]["status"] == "partial"
    # And the repeat-aware finished set is empty.
    assert runner._finished_doc_ids(suite_run_id, 2) == set()


def test_all_repeats_finished_marks_complete(env):
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 2)

    async def fake_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        _write_n_completed_runs(db, suite_run_id, doc["id"], 2)

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/run", json={"repeats": 2})
    suite_run_id = resp.json()["suite_run_id"]

    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["suite_run"]["status"] == "complete"


def test_resume_relaunches_doc_with_partial_repeats(env):
    """Resume must top up a document whose repeat group is incomplete, not
    skip it because one repeat finished (the peer-review false-finished bug)."""
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 2)
    from db import repository as repo

    conn = sqlite3.connect(str(db))
    suite_run_id = repo.create_suite_run(
        conn, suite_id=sid, config={"repeats": 2}
    )
    repo.update_suite_run_status(conn, suite_run_id, "partial", ended=True)
    conn.commit()
    conn.close()

    # doc0 finished BOTH repeats; doc1 finished only one.
    _write_n_completed_runs(db, suite_run_id, docs[0]["id"], 2)
    _write_completed_run(db, suite_run_id, docs[1]["id"])

    launched = []

    async def fake_launch(sr_id, doc, launch, api_key, proxy_url, model_name):
        launched.append(doc["id"])
        _write_completed_run(db, sr_id, doc["id"])  # the missing repeat

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/runs/{suite_run_id}/resume")
    assert resp.status_code == 200

    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert launched == [docs[1]["id"]]
    assert detail["suite_run"]["status"] == "complete"


def test_corpus_frozen_at_launch(env):
    """PLAN-evals-hardening Step 2: adding/removing suite documents after a
    suite run launched must not change what it resumes or how completion is
    judged — the run reads its v32 snapshot, never the live doc list."""
    srv, runner, db, tmp_path = env
    sid, docs = _make_suite_with_docs(srv, 2)
    from db import repository as repo

    launched = []

    async def fake_launch(sr_id, doc, launch, api_key, proxy_url, model_name):
        launched.append(doc["id"])
        _write_completed_run(db, sr_id, doc["id"])

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/run", json={})
    suite_run_id = resp.json()["suite_run_id"]
    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["suite_run"]["status"] == "complete"

    # Mutate the suite AFTER the run: add one doc, delete one original.
    conn = sqlite3.connect(str(db))
    new_doc = repo.add_suite_doc(
        conn, suite_id=sid, label="late", source_path="/tmp/late.pdf",
        source_filename="late.pdf",
    )
    repo.delete_suite_doc(conn, docs[0]["id"])
    conn.commit()
    conn.close()

    # Resume must not pick up the late doc (snapshot wins)…
    launched.clear()
    resp = tc.post(f"/api/suites/{sid}/runs/{suite_run_id}/resume")
    assert resp.status_code == 200
    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert new_doc not in launched
    # …and completion still counts the deleted original (still complete since
    # its child run finished before the deletion).
    assert detail["suite_run"]["status"] == "complete"
    # The snapshot still lists both original docs.
    state_ids = {d["doc_id"] for d in detail["doc_states"]}
    assert state_ids == {docs[0]["id"], docs[1]["id"]}


def test_deleting_doc_does_not_strand_snapshot_resume(env):
    """Peer-review Step 2: the snapshot must freeze the document BYTES, not just
    a path. Removing the live suite document through the real DELETE endpoint
    (which unlinks the managed source file) must leave a queued/partial run able
    to materialize its input on Resume."""
    srv, runner, db, tmp_path = env
    from db import repository as repo
    from pathlib import Path

    # A real managed source file on disk.
    src = tmp_path / "managed_doc.pdf"
    src.write_bytes(b"%PDF-1.4 real bytes")
    conn = sqlite3.connect(str(db))
    sid = repo.create_suite(conn, name="S")
    doc_id = repo.add_suite_doc(
        conn, suite_id=sid, label="d", source_path=str(src),
        source_filename="managed_doc.pdf",
    )
    conn.commit()
    conn.close()

    # Launch, but don't actually process — we only need the snapshot written.
    async def fake_launch(*a, **k):
        pass

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)
    suite_run_id = tc.post(f"/api/suites/{sid}/run", json={}).json()["suite_run_id"]
    for _ in range(100):
        if tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()[
            "suite_run"]["status"] != "running":
            break
        time.sleep(0.05)

    # The snapshot points at a run-owned COPY, not the managed original.
    conn = sqlite3.connect(str(db))
    snap = repo.list_suite_run_docs(conn, suite_run_id)[0]
    conn.close()
    snap_path = Path(snap["source_path"])
    assert snap_path != src
    assert snap_path.exists()

    # Delete the live document through the real endpoint (unlinks the original).
    assert tc.delete(f"/api/suites/{sid}/docs/{doc_id}").status_code == 200
    assert not src.exists()

    # Resume can still stage the input from the frozen copy.
    assert snap_path.exists()
    session_dir = tmp_path / "resume_session"
    runner._materialize_input(snap["source_path"], snap["source_filename"], session_dir)
    assert (session_dir / "uploaded.pdf").read_bytes() == b"%PDF-1.4 real bytes"


def test_notes_only_run_preserves_empty_statements(env):
    """Peer-review Step 5: an explicit empty statement list is a notes-only run,
    not a silent expansion back to all five (the maximum paid workload)."""
    _srv, runner, _db, _ = env
    cfg = runner._build_doc_config(
        {"statements": [], "notes_to_run": ["corporate_info"]}, {}
    )
    assert cfg.statements == []
    assert cfg.notes_to_run == ["corporate_info"]
    # A genuinely absent key still defaults to the full set.
    assert runner._build_doc_config({}, {}).statements == [
        "SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"
    ]


def test_empty_statements_and_notes_launch_rejected(env):
    """Nothing selected at all → 422 up front, not a silent full run."""
    srv, _runner, _db, _ = env
    sid, _docs = _make_suite_with_docs(srv, 1)
    tc = TestClient(srv.app)
    resp = tc.post(
        f"/api/suites/{sid}/run", json={"statements": [], "notes_to_run": []}
    )
    assert resp.status_code == 422


def test_materialize_failure_records_failed_doc_state(env):
    """A document that can't be staged (missing file) must be VISIBLE as a
    failed doc with a reason — not silently absent (peer-review Step 2)."""
    srv, runner, db, tmp_path = env
    sid, docs = _make_suite_with_docs(srv, 1)  # source_path points nowhere

    tc = TestClient(srv.app)
    resp = tc.post(f"/api/suites/{sid}/run", json={})
    assert resp.status_code == 200
    suite_run_id = resp.json()["suite_run_id"]

    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["suite_run"]["status"] == "partial"
    states = detail["doc_states"]
    assert len(states) == 1
    assert states[0]["state"] == "failed"
    assert "stage" in (states[0]["error"] or "").lower() or states[0]["error"]


def test_launch_derives_variants_from_benchmark(env, monkeypatch):
    """PLAN-evals-hardening Step 3: a doc attached to non-default-variant gold
    must extract THAT variant — the launch resolves it into the snapshot and
    the child run config; a contradicting explicit variant fails the launch."""
    srv, runner, db, _ = env
    sid, docs = _make_suite_with_docs(srv, 1)

    # Attach a benchmark id + per-doc denomination to the doc row.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE eval_suite_docs SET benchmark_id = 7, denomination = 'millions' "
        "WHERE id = ?", (docs[0]["id"],),
    )
    conn.commit()
    conn.close()

    import eval.variants as ev
    monkeypatch.setattr(
        ev, "benchmark_variants_for",
        lambda conn, bid, std, lvl: {"SOFP": "OrderOfLiquidity"} if bid == 7 else {},
    )

    captured = {}

    async def fake_launch(sr_id, doc, launch, api_key, proxy_url, model_name):
        captured["doc"] = doc
        captured["config"] = runner._build_doc_config(launch, doc)
        _write_completed_run(db, sr_id, doc["id"])

    runner._launch_one_document = fake_launch
    tc = TestClient(srv.app)

    # Contradicting explicit variant → 422, nothing launched.
    resp = tc.post(f"/api/suites/{sid}/run",
                   json={"variants": {"SOFP": "CuNonCu"}})
    assert resp.status_code == 422
    assert "variant" in resp.json()["detail"].lower()

    # Clean launch: derived variant + per-doc denomination reach the config.
    resp = tc.post(f"/api/suites/{sid}/run", json={})
    assert resp.status_code == 200, resp.text
    suite_run_id = resp.json()["suite_run_id"]
    for _ in range(100):
        detail = tc.get(f"/api/suites/{sid}/runs/{suite_run_id}").json()
        if detail["suite_run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert captured["doc"]["variants"] == {"SOFP": "OrderOfLiquidity"}
    cfg = captured["config"]
    assert cfg.variants["SOFP"] == "OrderOfLiquidity"
    assert cfg.denomination == "millions"
    assert cfg.benchmark_id == 7


def _write_history_run(db, *, seconds, tokens, cost, model="m1"):
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
        "started_at, ended_at) VALUES ('t', 'x.pdf', 'completed', 'h', ?, ?)",
        ("2026-01-01T00:00:00",
         f"2026-01-01T00:{seconds // 60:02d}:{seconds % 60:02d}"),
    )
    # Tokens/cost live on run_agents (gotcha #6) — one agent row is enough.
    conn.execute(
        "INSERT INTO run_agents(run_id, statement_type, status, model, "
        "total_tokens, total_cost, started_at) "
        "VALUES (?, 'SOFP', 'succeeded', ?, ?, ?, 't')",
        (cur.lastrowid, model, tokens, cost),
    )
    conn.commit()
    conn.close()


def test_estimate_sequential_repeats_and_cost(env):
    """Step 4 (PLAN-evals-hardening): repeats run sequentially in one slot, so
    1 doc × 5 repeats ≈ 5× a run's duration (the old runs÷3 formula said
    ~1.7×); and the estimate must carry token + cost figures."""
    srv, runner, db, _ = env
    sid, _docs = _make_suite_with_docs(srv, 1)
    _write_history_run(db, seconds=600, tokens=100_000, cost=2.0)

    tc = TestClient(srv.app)
    est = tc.post(f"/api/suites/{sid}/estimate", json={"repeats": 5}).json()
    assert est["extraction_runs"] == 5
    # ceil(1/3) × 5 × 600s = 3000s — NOT 5/3 × 600 = 1000s.
    assert est["estimated_wall_seconds"] == pytest.approx(3000, rel=0.01)
    assert est["estimated_tokens"] == 500_000
    assert est["estimated_cost_usd"] == pytest.approx(10.0)
    assert est["cost_range_usd"] == [10.0, 10.0]


def test_estimate_parallel_docs_single_repeat(env):
    """6 docs × 1 repeat at concurrency 3 ≈ 2 batches ≈ 2× avg duration."""
    srv, runner, db, _ = env
    sid, _docs = _make_suite_with_docs(srv, 6)
    _write_history_run(db, seconds=300, tokens=50_000, cost=1.0)

    tc = TestClient(srv.app)
    est = tc.post(f"/api/suites/{sid}/estimate", json={}).json()
    assert est["estimated_wall_seconds"] == pytest.approx(600, rel=0.01)
    assert est["estimated_tokens"] == 300_000


def test_estimate_prefers_same_model_history(env):
    """A model with its own history estimates from it, not the global mix."""
    srv, runner, db, _ = env
    sid, _docs = _make_suite_with_docs(srv, 1)
    _write_history_run(db, seconds=60, tokens=10_000, cost=0.5, model="cheap")
    _write_history_run(db, seconds=600, tokens=200_000, cost=8.0, model="fancy")

    tc = TestClient(srv.app)
    est = tc.post(f"/api/suites/{sid}/estimate",
                  json={"model": "fancy"}).json()
    assert est["estimated_tokens"] == 200_000
    assert est["estimated_cost_usd"] == pytest.approx(8.0)


def test_estimate_resolves_default_model_not_mixed_history(env):
    """Peer-review Step 6: the default-model UI path sends model=null. The
    estimate must resolve it to TEST_MODEL and sample THAT model's history, not
    the global mix — and report which model it assumed."""
    srv, runner, db, _ = env  # env sets TEST_MODEL=test-model
    sid, _docs = _make_suite_with_docs(srv, 1)
    _write_history_run(db, seconds=600, tokens=200_000, cost=8.0, model="test-model")
    _write_history_run(db, seconds=60, tokens=10_000, cost=0.5, model="other-model")

    tc = TestClient(srv.app)
    est = tc.post(f"/api/suites/{sid}/estimate", json={}).json()
    assert est["estimate_model"] == "test-model"
    assert est["estimate_model_filtered"] is True
    # Figures come from the test-model run only, not the cheap other-model one.
    assert est["estimated_tokens"] == 200_000
    assert est["estimated_cost_usd"] == pytest.approx(8.0)


def test_global_cap_across_concurrent_suite_runs(env):
    """Step 15 (PLAN-evals-hardening): the 3-slot cap is GLOBAL — two suite
    runs at once must never exceed 3 in-flight documents combined."""
    srv, runner, db, _ = env

    state = {"cur": 0, "max": 0}

    async def fake_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.03)
        state["cur"] -= 1

    runner._launch_one_document = fake_launch
    _sid1, docs1 = _make_suite_with_docs(srv, 4)
    _sid2, docs2 = _make_suite_with_docs(srv, 4)

    async def both():
        await asyncio.gather(
            runner._process_documents(1, docs1, {}, "k", "", "m"),
            runner._process_documents(2, docs2, {}, "k", "", "m"),
        )

    asyncio.run(both())
    assert state["max"] <= 3


def test_second_launch_of_running_suite_is_409(env):
    srv, runner, db, _ = env
    sid, _docs = _make_suite_with_docs(srv, 1)

    started = threading.Event()
    release = threading.Event()

    async def slow_launch(suite_run_id, doc, launch, api_key, proxy_url, model_name):
        started.set()
        await asyncio.get_event_loop().run_in_executor(None, release.wait)

    runner._launch_one_document = slow_launch
    tc = TestClient(srv.app)
    first = tc.post(f"/api/suites/{sid}/run", json={})
    assert first.status_code == 200
    assert started.wait(5)
    try:
        second = tc.post(f"/api/suites/{sid}/run", json={})
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]
    finally:
        release.set()


def test_crash_reconcile_retires_running_doc_states(env):
    """A doc row stuck 'running' after a crash reads failed('server
    restarted'); queued rows stay queued so Resume relaunches them."""
    srv, runner, db, _ = env
    from db import repository as repo

    conn = sqlite3.connect(str(db))
    sid = repo.create_suite(conn, name="S")
    sr = repo.create_suite_run(conn, suite_id=sid, config={})
    conn.execute(
        "INSERT INTO eval_suite_run_docs(suite_run_id, suite_doc_id, label, state) "
        "VALUES (?, 1, 'a', 'running'), (?, 2, 'b', 'queued')",
        (sr, sr),
    )
    conn.commit()

    repo.reconcile_stale_suite_runs(conn)
    conn.commit()
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT suite_doc_id, state, error FROM eval_suite_run_docs "
            "WHERE suite_run_id = ?", (sr,),
        )
    }
    conn.close()
    assert rows[1] == ("failed", "server restarted")
    assert rows[2] == ("queued", None)
