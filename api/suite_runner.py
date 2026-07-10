"""Suite batch runner (Evals workspace, Step E3).

Launches a whole suite as one batch: N documents (× repeats) run through the
NORMAL pipeline with limited parallelism (3 documents at a time), each child a
completely ordinary run (audit row, traces, terminal-status guarantee — gotcha
#10). The runner is a background loop in the server process (same pattern as the
reviewer pass), not a job queue.

Contracts:
  * concurrency fixed at 3 (PRD decision #2);
  * partial-on-crash: a suite run left 'running' is retired to 'partial' at
    startup (repo.reconcile_stale_suite_runs) and offers Resume;
  * Resume re-launches ONLY documents without a finished child run in this
    suite run — identified by a deterministic per-doc session id;
  * a document whose run fails is marked failed and the batch continues; the
    aggregate states "N of M" (eval/scorecards.aggregate_suite).

Endpoints:
  POST /api/suites/{id}/run                       — launch a batch (async)
  POST /api/suites/{id}/runs/{sr}/resume          — re-launch the remainder
  POST /api/suites/{id}/runs/{sr}/stop            — abort remaining launches
  GET  /api/suites/{id}/runs/{sr}                  — detail + per-doc scorecards + aggregate
  POST /api/suites/{id}/estimate                   — pre-launch cost/time estimate
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import server
from server import RunConfigRequest

logger = logging.getLogger("server")

router = APIRouter()

SUITE_CONCURRENCY = 3

# Per-suite-run cancel flags so Stop can abort remaining launches (the in-flight
# runs finalize themselves via task_registry / their own finally — gotcha #10).
_active_batches: dict[int, threading.Event] = {}


class SuiteRunLaunch(BaseModel):
    label: str = ""
    model: Optional[str] = None
    statements: list[str] = ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]
    variants: dict[str, str] = {}
    use_scout: bool = False
    notes_to_run: list[str] = []
    repeats: int = 1


def _doc_session_id(suite_run_id: int, doc_id: int) -> str:
    """Deterministic per-document session id — encodes the doc so Resume can
    tell which documents already finished without a runs↔docs mapping table."""
    return f"suite-{suite_run_id}-doc-{doc_id}"


def _materialize_input(source_path: str, source_filename: str, session_dir: Path) -> None:
    """Place the document's input as session_dir/uploaded.pdf (converting a
    .docx at the door, exactly like the upload endpoint). Raises on failure."""
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "original_filename.txt").write_text(
        source_filename or "uploaded.pdf", encoding="utf-8"
    )
    dest = session_dir / "uploaded.pdf"
    if source_path.lower().endswith(".docx"):
        from ingest.word_convert import convert_docx_to_pdf
        convert_docx_to_pdf(Path(source_path), dest)
    else:
        shutil.copy2(source_path, dest)


def _build_doc_config(launch: dict, doc: dict) -> RunConfigRequest:
    """A per-document RunConfigRequest from the suite-run launch config + the
    document's filing standard/level + optional gold benchmark."""
    return RunConfigRequest(
        statements=launch.get("statements") or ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"],
        variants=launch.get("variants") or {},
        use_scout=bool(launch.get("use_scout")),
        notes_to_run=launch.get("notes_to_run") or [],
        model=launch.get("model"),
        filing_standard=doc.get("filing_standard", "mfrs"),
        filing_level=doc.get("filing_level", "company"),
        benchmark_id=doc.get("benchmark_id"),
        repeats=int(launch.get("repeats", 1) or 1),
    )


async def _drain_stream(agen) -> None:
    """Drive a run stream to completion with no client attached (the run still
    merges + grades + finalizes — 'runs outlive sessions')."""
    try:
        async for _ in agen:
            pass
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass


async def _launch_one_document(
    suite_run_id: int, doc: dict, launch: dict,
    api_key: str, proxy_url: str, model_name: str,
) -> None:
    """Run a single document (the injectable unit). Sets up an isolated session,
    materializes the input, and streams a normal run to completion linked to the
    suite run. Any exception marks the doc failed (a bad doc never poisons the
    batch)."""
    session_id = _doc_session_id(suite_run_id, doc["id"])
    session_dir = server.OUTPUT_DIR / session_id
    try:
        _materialize_input(doc["source_path"], doc.get("source_filename", ""), session_dir)
    except Exception:
        logger.warning("suite %s: failed to materialize doc %s",
                       suite_run_id, doc["id"], exc_info=True)
        return
    run_config = _build_doc_config(launch, doc)
    server.active_runs.add(session_id)
    try:
        if run_config.repeats and run_config.repeats > 1:
            # Repeats within a suite: the group stream owns its child rows; they
            # inherit suite_run_id via run_multi_agent_stream's create path.
            agen = server.run_repeat_group_stream(
                session_id=session_id, session_dir=session_dir,
                run_config=run_config, api_key=api_key, proxy_url=proxy_url,
                model_name=model_name, suite_run_id=suite_run_id,
            )
        else:
            agen = server.run_multi_agent_stream(
                session_id=session_id, session_dir=session_dir,
                run_config=run_config, api_key=api_key, proxy_url=proxy_url,
                model_name=model_name, suite_run_id=suite_run_id,
            )
        await _drain_stream(agen)
    finally:
        server.active_runs.discard(session_id)


async def _process_documents(
    suite_run_id: int, docs: list[dict], launch: dict,
    api_key: str, proxy_url: str, model_name: str,
) -> None:
    """Run `docs` with a concurrency cap of SUITE_CONCURRENCY, honouring the
    cancel flag between acquisitions."""
    cancel = _active_batches.get(suite_run_id)
    sem = asyncio.Semaphore(SUITE_CONCURRENCY)

    async def _one(doc):
        async with sem:
            if cancel is not None and cancel.is_set():
                return
            await _launch_one_document(
                suite_run_id, doc, launch, api_key, proxy_url, model_name
            )

    await asyncio.gather(*[_one(d) for d in docs], return_exceptions=True)


def _finished_doc_ids(suite_run_id: int) -> set[int]:
    """Doc ids whose deterministic session already has a TERMINAL child run in
    this suite run — used by Resume to skip completed documents."""
    conn = server._open_audit_conn()
    try:
        rows = conn.execute(
            "SELECT session_id FROM runs WHERE suite_run_id = ? "
            "AND status IN ('completed','completed_with_errors')",
            (suite_run_id,),
        ).fetchall()
    finally:
        conn.close()
    done: set[int] = set()
    for (sid,) in rows:
        # session id shape: suite-{sr}-doc-{doc_id}
        try:
            done.add(int(str(sid).rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            pass
    return done


def _run_batch_thread(suite_run_id: int, suite_id: int, launch: dict,
                      *, resume: bool) -> None:
    """Background thread body: process the suite's documents, then finalize the
    suite-run status. Mirrors the reviewer pass's dedicated-thread pattern."""
    from db import repository as repo

    def _thread_main() -> None:
        try:
            load_dotenv(server.ENV_FILE, override=True)
            api_key = server._resolve_api_key()
            proxy_url = os.environ.get("LLM_PROXY_URL", "")
            model_name = launch.get("model") or os.environ.get("TEST_MODEL", "openai.gpt-5.4")

            conn = server._open_audit_conn()
            try:
                docs = repo.list_suite_docs(conn, suite_id)
            finally:
                conn.close()

            if resume:
                done = _finished_doc_ids(suite_run_id)
                docs = [d for d in docs if d["id"] not in done]

            asyncio.run(_process_documents(
                suite_run_id, docs, launch, api_key, proxy_url, model_name
            ))

            # Finalize: complete when every document produced a finished child
            # run, else partial (a failed/aborted doc, or a Stop).
            conn = server._open_audit_conn()
            try:
                all_docs = repo.list_suite_docs(conn, suite_id)
                finished = _finished_doc_ids(suite_run_id)
                cancelled = suite_run_id in _active_batches and _active_batches[suite_run_id].is_set()
                status = (
                    "complete"
                    if len(finished) >= len(all_docs) and not cancelled and all_docs
                    else "partial"
                )
                repo.update_suite_run_status(conn, suite_run_id, status, ended=True)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.exception("suite batch %s failed", suite_run_id)
            try:
                conn = server._open_audit_conn()
                try:
                    repo.update_suite_run_status(conn, suite_run_id, "failed", ended=True)
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                logger.warning("could not mark suite run %s failed", suite_run_id)
        finally:
            _active_batches.pop(suite_run_id, None)

    _active_batches[suite_run_id] = threading.Event()
    threading.Thread(
        target=_thread_main, name=f"suite-batch-{suite_run_id}", daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------
def _recent_avg_run_seconds() -> Optional[float]:
    """Rough wall-clock estimate: the average duration of recent finished runs."""
    conn = server._open_audit_conn()
    try:
        rows = conn.execute(
            "SELECT started_at, ended_at FROM runs "
            "WHERE status IN ('completed','completed_with_errors') "
            "AND started_at != '' AND ended_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    from db import repository as repo
    durs = [
        d for d in (repo._parse_iso_duration(s or "", e or "") for s, e in rows)
        if d is not None
    ]
    return (sum(durs) / len(durs)) if durs else None


@router.post("/api/suites/{suite_id}/estimate")
async def estimate_suite_run_endpoint(suite_id: int, body: SuiteRunLaunch):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suite = repo.get_suite(conn, suite_id)
    finally:
        conn.close()
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    n_docs = len(suite["docs"])
    repeats = max(1, min(5, int(body.repeats or 1)))
    n_runs = n_docs * repeats
    avg = _recent_avg_run_seconds()
    # Wall-clock ≈ (runs / concurrency) × avg run duration.
    wall = (n_runs / SUITE_CONCURRENCY) * avg if avg else None
    return {
        "documents": n_docs,
        "repeats": repeats,
        "extraction_runs": n_runs,
        "avg_run_seconds": avg,
        "estimated_wall_seconds": wall,
        "concurrency": SUITE_CONCURRENCY,
    }


@router.post("/api/suites/{suite_id}/run")
async def launch_suite_run_endpoint(suite_id: int, body: SuiteRunLaunch):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suite = repo.get_suite(conn, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Suite not found")
        if not suite["docs"]:
            raise HTTPException(status_code=422, detail="Add at least one document first.")
        launch = body.model_dump()
        suite_run_id = repo.create_suite_run(
            conn, suite_id=suite_id, label=body.label, config=launch,
            model=body.model,
        )
        conn.commit()
    finally:
        conn.close()

    _run_batch_thread(suite_run_id, suite_id, launch, resume=False)
    return {"suite_run_id": suite_run_id, "status": "running"}


@router.post("/api/suites/{suite_id}/runs/{suite_run_id}/resume")
async def resume_suite_run_endpoint(suite_id: int, suite_run_id: int):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        sr = repo.get_suite_run(conn, suite_run_id)
        if sr is None or sr["suite_id"] != suite_id:
            raise HTTPException(status_code=404, detail="Suite run not found")
        if sr["status"] == "running":
            raise HTTPException(status_code=409, detail="This suite run is still running.")
        launch = sr.get("config") or {}
        repo.update_suite_run_status(conn, suite_run_id, "running")
        conn.commit()
    finally:
        conn.close()
    _run_batch_thread(suite_run_id, suite_id, launch, resume=True)
    return {"suite_run_id": suite_run_id, "status": "running"}


@router.post("/api/suites/{suite_id}/runs/{suite_run_id}/stop")
async def stop_suite_run_endpoint(suite_id: int, suite_run_id: int):
    flag = _active_batches.get(suite_run_id)
    if flag is not None:
        flag.set()
    # Also cancel any in-flight child runs for this suite run.
    import task_registry
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        sr = repo.get_suite_run(conn, suite_run_id)
        if sr is None or sr["suite_id"] != suite_id:
            raise HTTPException(status_code=404, detail="Suite run not found")
        running = conn.execute(
            "SELECT session_id FROM runs WHERE suite_run_id = ? AND status = 'running'",
            (suite_run_id,),
        ).fetchall()
    finally:
        conn.close()
    for (sid,) in running:
        try:
            task_registry.cancel_all(sid)
        except Exception:
            pass
    return {"stopping": suite_run_id}


@router.get("/api/suites/{suite_id}/runs/{suite_run_id}")
async def get_suite_run_endpoint(suite_id: int, suite_run_id: int):
    """Suite-run detail: status + per-document scorecards + the aggregate."""
    from db import repository as repo
    from eval.scorecards import build_document_scorecard, aggregate_suite

    conn = server._open_audit_conn()
    try:
        sr = repo.get_suite_run(conn, suite_run_id)
        if sr is None or sr["suite_id"] != suite_id:
            raise HTTPException(status_code=404, detail="Suite run not found")
        cards = []
        for child in sr["runs"]:
            card = build_document_scorecard(conn, child["id"])
            if card is not None:
                cards.append(card)
    finally:
        conn.close()

    aggregate = aggregate_suite(cards)
    return {
        "suite_run": {k: v for k, v in sr.items() if k != "runs"},
        "documents": [c.to_dict() for c in cards],
        "aggregate": aggregate,
    }
