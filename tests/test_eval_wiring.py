"""Phase 4 wiring tests — benchmark_id threading + end-of-run grading.

Covers the seams the full pipeline relies on, without standing up the whole
``run_multi_agent_stream`` generator:

* ``RunConfigRequest`` / ``RunConfigPatchRequest`` / ``RunConfig`` accept a
  ``benchmark_id``.
* ``server._grade_run_against_benchmark`` grades a finished run and persists a
  scorecard (the hook called at run completion).
* ``repo.list_runs`` surfaces the eval score + benchmark_id for the History
  list, and a non-eval run leaves them None.
"""
from __future__ import annotations

import sqlite3

from db.schema import init_db
from db import repository as repo


def test_run_config_models_accept_benchmark_id():
    import server
    from coordinator import RunConfig

    req = server.RunConfigRequest(statements=["SOFP"], benchmark_id=7)
    assert req.benchmark_id == 7
    # Default stays None for a normal run.
    assert server.RunConfigRequest(statements=["SOFP"]).benchmark_id is None

    patch = server.RunConfigPatchRequest(benchmark_id=9)
    assert patch.benchmark_id == 9

    cfg = RunConfig(pdf_path="x", output_dir="y", benchmark_id=3)
    assert cfg.benchmark_id == 3


def _seed_eval_db(tmp_path):
    """Seed a DB with one benchmark, one template+leaf concept, gold, and a
    completed run with matching facts. Returns (db_path, run_id, bench_id)."""
    db = tmp_path / "wire.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) "
        "VALUES ('tpl', '/tmp/t.xlsx')"
    )
    for uuid in ("c1", "c2"):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, 'tpl', 'LEAF', ?, 'SOFP', 5, 'B')",
            (uuid, uuid),
        )
    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
        "VALUES ('B', 'mfrs', 'company')"
    )
    bench_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
        "statement_type) VALUES (?, 'tpl', 'SOFP')",
        (bench_id,),
    )
    for uuid, val in (("c1", 100.0), ("c2", 200.0)):
        conn.execute(
            "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, "
            "period, entity_scope, value, value_status) "
            "VALUES (?, ?, 'CY', 'Company', ?, 'observed')",
            (bench_id, uuid, val),
        )
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
        "ended_at, benchmark_id) VALUES "
        "('2026-06-04T00:00:00Z', 'x.pdf', 'completed', "
        "'2026-06-04T00:00:00Z', '2026-06-04T00:01:00Z', ?)",
        (bench_id,),
    )
    run_id = int(cur.lastrowid)
    # Run facts: c1 matches gold, c2 is wrong.
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) "
        "VALUES (?, 'c1', 'CY', 'Company', 100.0, 'observed')",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) "
        "VALUES (?, 'c2', 'CY', 'Company', 999.0, 'observed')",
        (run_id,),
    )
    conn.commit()
    conn.close()
    return str(db), run_id, bench_id


def _build_run(srv, db_conn, run_config):
    """Drive _validate_and_build_run with a stubbed model + a real run row."""
    import tempfile
    from pathlib import Path

    cur = db_conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-06-05T00:00:00Z', 'x.pdf', 'running')"
    )
    db_conn.commit()
    run_id = int(cur.lastrowid)
    return srv._validate_and_build_run(
        run_config=run_config,
        api_key="k",
        proxy_url="",
        model_name="test-model",
        session_dir=Path(tempfile.gettempdir()),
        output_dir=tempfile.gettempdir(),
        session_id="sess",
        run_id=run_id,
        db_conn=db_conn,
    )


def _seed_benchmark_row(db_conn, standard, level):
    cur = db_conn.execute(
        "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
        "VALUES (?, ?, ?)",
        (f"B-{standard}-{level}", standard, level),
    )
    db_conn.commit()
    return int(cur.lastrowid)


def test_validate_fails_fast_on_benchmark_mismatch_and_missing(tmp_path, monkeypatch):
    """A stale/mismatched or nonexistent benchmark_id fails the run at
    validation (before extraction), not silently at grade time (P1)."""
    import importlib
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    srv._CANONICAL_BOOTSTRAP_OK = True
    monkeypatch.setattr(srv, "_create_proxy_model", lambda *a, **k: object())

    from db.schema import init_db
    init_db(db)
    conn = srv._open_audit_conn()
    try:
        # A Group benchmark attached to a Company run → mismatch → fail fast.
        group_bench = _seed_benchmark_row(conn, "mfrs", "group")
        req = srv.RunConfigRequest(
            statements=["SOFP"], filing_standard="mfrs", filing_level="company",
            benchmark_id=group_bench,
        )
        validated, events, status = _build_run(srv, conn, req)
        assert validated is None
        assert status == "failed"
        assert any("group" in str(e["data"].get("message", "")).lower()
                   for e in events)

        # A nonexistent benchmark → fail fast with a not-found message.
        req2 = srv.RunConfigRequest(
            statements=["SOFP"], filing_standard="mfrs", filing_level="company",
            benchmark_id=99999,
        )
        validated2, events2, _ = _build_run(srv, conn, req2)
        assert validated2 is None
        assert any("not found" in str(e["data"].get("message", "")).lower()
                   for e in events2)

        # A matching benchmark validates cleanly and threads onto the config.
        ok_bench = _seed_benchmark_row(conn, "mfrs", "company")
        req3 = srv.RunConfigRequest(
            statements=["SOFP"], filing_standard="mfrs", filing_level="company",
            benchmark_id=ok_bench,
        )
        validated3, _, _ = _build_run(srv, conn, req3)
        assert validated3 is not None
        assert validated3.config.benchmark_id == ok_bench

        # No benchmark at all → unaffected (normal run).
        req4 = srv.RunConfigRequest(
            statements=["SOFP"], filing_standard="mfrs", filing_level="company",
        )
        validated4, _, _ = _build_run(srv, conn, req4)
        assert validated4 is not None
        assert validated4.config.benchmark_id is None
    finally:
        conn.close()


def test_grade_hook_persists_scorecard(tmp_path):
    import server

    db_path, run_id, bench_id = _seed_eval_db(tmp_path)
    score = server._grade_run_against_benchmark(db_path, run_id, bench_id)
    assert score is not None
    assert score["gold_cells"] == 2
    assert score["matched_cells"] == 1
    assert score["mismatch_cells"] == 1
    assert abs(score["score"] - 0.5) < 1e-9

    # Persisted: a fresh fetch returns the same row.
    conn = sqlite3.connect(db_path)
    try:
        again = repo.fetch_eval_score(conn, run_id, bench_id)
        assert again["matched_cells"] == 1
    finally:
        conn.close()


def test_grade_hook_soft_fails_on_bad_benchmark(tmp_path):
    """A grading error returns None instead of raising — the run must finish."""
    import server

    db_path, run_id, _ = _seed_eval_db(tmp_path)
    # Benchmark 99999 has no template set → grade_run returns an empty card;
    # but pass a non-existent DB path to force the except branch.
    assert server._grade_run_against_benchmark("/no/such.db", run_id, 1) is None


def test_list_runs_surfaces_eval_score(tmp_path):
    db_path, run_id, bench_id = _seed_eval_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Grade + persist, then list.
        import server
        server._grade_run_against_benchmark(db_path, run_id, bench_id)

        summaries = repo.list_runs(conn, limit=10, offset=0)
        graded = next(s for s in summaries if s.id == run_id)
        assert graded.benchmark_id == bench_id
        assert abs(graded.eval_score - 0.5) < 1e-9

        # A second, non-eval run leaves both None.
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-06-04T00:02:00Z', 'plain.pdf', 'completed')"
        )
        conn.commit()
        plain_id = int(cur.lastrowid)
        summaries = repo.list_runs(conn, limit=10, offset=0)
        plain = next(s for s in summaries if s.id == plain_id)
        assert plain.benchmark_id is None
        assert plain.eval_score is None
    finally:
        conn.close()
