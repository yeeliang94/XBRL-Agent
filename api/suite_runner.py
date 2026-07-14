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


def _sha256_of(path: str) -> str:
    """Best-effort content hash for the corpus snapshot (empty on any failure —
    a missing file will surface as a failed doc at materialize time anyway)."""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _set_doc_state(suite_run_id: int, doc_id: int, state: str,
                   error: Optional[str] = None) -> None:
    """Persist a snapshot doc's execution state (best-effort — state tracking
    must never take down the batch)."""
    from db import repository as repo
    try:
        conn = server._open_audit_conn()
        try:
            repo.update_suite_run_doc_state(
                conn, suite_run_id, doc_id, state, error=error
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("suite %s: could not set doc %s state=%s",
                       suite_run_id, doc_id, state, exc_info=True)


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
    document's filing standard/level/denomination + optional gold benchmark.
    The document's benchmark-derived variants (resolved at launch into the
    corpus snapshot) override the launch-level defaults — a run graded against
    non-default-variant gold MUST extract that variant (gotcha #21/#23)."""
    variants = dict(launch.get("variants") or {})
    variants.update(doc.get("variants") or {})
    return RunConfigRequest(
        statements=launch.get("statements") or ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"],
        variants=variants,
        use_scout=bool(launch.get("use_scout")),
        notes_to_run=launch.get("notes_to_run") or [],
        model=launch.get("model"),
        filing_standard=doc.get("filing_standard", "mfrs"),
        filing_level=doc.get("filing_level", "company"),
        denomination=doc.get("denomination") or "thousands",
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


def _repeat_progress(suite_run_id: int, doc_id: int) -> tuple[int, Optional[int]]:
    """(finished_repeat_count, latest repeat_group_id) for a document's session
    in this suite run — how Resume knows where a partially-run repeat group
    stopped so it can top up ONLY the missing repeats into the SAME group."""
    session_id = _doc_session_id(suite_run_id, doc_id)
    conn = server._open_audit_conn()
    try:
        finished = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE suite_run_id = ? AND session_id = ? "
            "AND status IN ('completed','completed_with_errors')",
            (suite_run_id, session_id),
        ).fetchone()[0]
        grp = conn.execute(
            "SELECT repeat_group_id FROM runs WHERE suite_run_id = ? "
            "AND session_id = ? AND repeat_group_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (suite_run_id, session_id),
        ).fetchone()
    finally:
        conn.close()
    return int(finished), (int(grp[0]) if grp else None)


async def _launch_one_document(
    suite_run_id: int, doc: dict, launch: dict,
    api_key: str, proxy_url: str, model_name: str,
) -> None:
    """Run a single document (the injectable unit). Sets up an isolated session,
    materializes the input, and streams a normal run to completion linked to the
    suite run. Any exception marks the doc failed (a bad doc never poisons the
    batch); every exit records the doc's state on the corpus snapshot."""
    session_id = _doc_session_id(suite_run_id, doc["id"])
    session_dir = server.OUTPUT_DIR / session_id
    _set_doc_state(suite_run_id, doc["id"], "running")
    try:
        _materialize_input(doc["source_path"], doc.get("source_filename", ""), session_dir)
    except Exception as exc:
        logger.warning("suite %s: failed to materialize doc %s",
                       suite_run_id, doc["id"], exc_info=True)
        _set_doc_state(
            suite_run_id, doc["id"], "failed",
            f"Could not stage the document for extraction: {exc}",
        )
        return
    run_config = _build_doc_config(launch, doc)
    repeats = max(1, int(run_config.repeats or 1))
    cancel = _active_batches.get(suite_run_id)
    server.active_runs.add(session_id)
    try:
        if repeats > 1:
            # Repeats within a suite: the group stream owns its child rows; they
            # inherit suite_run_id via run_multi_agent_stream's create path. On
            # Resume, top up ONLY the missing repeats into the existing group so
            # consistency is scored over the full requested set.
            done, group_id = _repeat_progress(suite_run_id, doc["id"])
            if done >= repeats:
                _set_doc_state(suite_run_id, doc["id"], "finished")
                return
            agen = server.run_repeat_group_stream(
                session_id=session_id, session_dir=session_dir,
                run_config=run_config, api_key=api_key, proxy_url=proxy_url,
                model_name=model_name, suite_run_id=suite_run_id,
                existing_group_id=group_id, start_index=done,
                should_abort=(cancel.is_set if cancel is not None else None),
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
        done, _ = _repeat_progress(suite_run_id, doc["id"])
        if done >= repeats:
            _set_doc_state(suite_run_id, doc["id"], "finished")
        else:
            _set_doc_state(
                suite_run_id, doc["id"], "failed",
                f"{done} of {repeats} repeat(s) completed",
            )


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


def _finished_doc_ids(suite_run_id: int, repeats: int = 1) -> set[int]:
    """Doc ids whose deterministic session has ALL requested repeats finished
    in this suite run — used by Resume to skip completed documents and by
    finalize to judge complete-vs-partial. A doc configured for 3 repeats with
    1 finished is NOT done (the peer-review repeat-completion fix): Resume must
    top up the missing repeats, and the suite must not report complete."""
    repeats = max(1, int(repeats or 1))
    conn = server._open_audit_conn()
    try:
        rows = conn.execute(
            "SELECT session_id, COUNT(*) FROM runs WHERE suite_run_id = ? "
            "AND status IN ('completed','completed_with_errors') "
            "GROUP BY session_id",
            (suite_run_id,),
        ).fetchall()
    finally:
        conn.close()
    done: set[int] = set()
    for sid, count in rows:
        if int(count) < repeats:
            continue
        # session id shape: suite-{sr}-doc-{doc_id}
        try:
            done.add(int(str(sid).rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            pass
    return done


def _snapshot_docs(suite_run_id: int, suite_id: int) -> list[dict]:
    """The suite run's frozen corpus. Falls back to (and backfills from) the
    live suite docs for suite runs created before the v32 snapshot existed, so
    Resume on an old partial run still works."""
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        docs = repo.list_suite_run_docs(conn, suite_run_id)
        if not docs:
            live = repo.list_suite_docs(conn, suite_id)
            if live:
                for d in live:
                    d["source_sha256"] = _sha256_of(d.get("source_path", ""))
                repo.snapshot_suite_run_docs(conn, suite_run_id, live)
                conn.commit()
                docs = repo.list_suite_run_docs(conn, suite_run_id)
    finally:
        conn.close()
    return docs


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
            repeats = max(1, min(5, int(launch.get("repeats", 1) or 1)))

            # The frozen corpus — NEVER the live suite docs (Step 2): editing
            # the suite after launch must not change what this run processes.
            docs = _snapshot_docs(suite_run_id, suite_id)

            if resume:
                done = _finished_doc_ids(suite_run_id, repeats)
                docs = [d for d in docs if d["id"] not in done]

            asyncio.run(_process_documents(
                suite_run_id, docs, launch, api_key, proxy_url, model_name
            ))

            # Finalize: complete when every snapshot document finished ALL its
            # repeats, else partial (a failed/aborted doc, or a Stop).
            conn = server._open_audit_conn()
            try:
                all_docs = repo.list_suite_run_docs(conn, suite_run_id)
                finished = _finished_doc_ids(suite_run_id, repeats)
                cancelled = suite_run_id in _active_batches and _active_batches[suite_run_id].is_set()
                status = (
                    "complete"
                    if all_docs and not cancelled
                    and all(d["id"] in finished for d in all_docs)
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
def _recent_run_stats(model: Optional[str] = None) -> dict:
    """Average duration / tokens / cost of recent finished runs, preferring
    runs of the SAME model (a Haiku baseline says nothing about an Opus
    suite). Falls back to any recent finished run when the model has no
    history. Token/cost sample min–max feed the estimate's range."""
    conn = server._open_audit_conn()
    try:
        # Tokens/cost live on run_agents (per-agent rollups, gotcha #6) — the
        # runs table itself carries no totals, so aggregate per run here.
        base = (
            "SELECT r.started_at, r.ended_at, "
            "(SELECT SUM(a.total_tokens) FROM run_agents a WHERE a.run_id = r.id), "
            "(SELECT SUM(a.total_cost) FROM run_agents a WHERE a.run_id = r.id) "
            "FROM runs r "
            "WHERE r.status IN ('completed','completed_with_errors') "
            "AND r.started_at != '' AND r.ended_at IS NOT NULL "
        )
        rows: list = []
        if model:
            rows = conn.execute(
                base + "AND EXISTS (SELECT 1 FROM run_agents a WHERE "
                "a.run_id = r.id AND a.model = ?) ORDER BY r.id DESC LIMIT 20",
                (model,),
            ).fetchall()
        if not rows:
            rows = conn.execute(base + "ORDER BY r.id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    from db import repository as repo

    durs = [
        d for d in (
            repo._parse_iso_duration(s or "", e or "") for s, e, _t, _c in rows
        )
        if d is not None
    ]
    tokens = [int(t) for _s, _e, t, _c in rows if t]
    costs = [float(c) for _s, _e, _t, c in rows if c]
    return {
        "avg_seconds": (sum(durs) / len(durs)) if durs else None,
        "avg_tokens": (sum(tokens) / len(tokens)) if tokens else None,
        "avg_cost": (sum(costs) / len(costs)) if costs else None,
        "tokens_range": (min(tokens), max(tokens)) if tokens else None,
        "cost_range": (min(costs), max(costs)) if costs else None,
        "sample_size": len(rows),
    }


@router.post("/api/suites/{suite_id}/estimate")
async def estimate_suite_run_endpoint(suite_id: int, body: SuiteRunLaunch):
    from math import ceil

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
    stats = _recent_run_stats(body.model)
    avg = stats["avg_seconds"]
    # Wall-clock: documents share the concurrency slots, but a document's
    # repeats run SEQUENTIALLY inside its one slot (run_repeat_group_stream is
    # sequential-in-one-stream by design) — so repeats multiply the wall time,
    # they don't parallelize. The old runs/concurrency formula underestimated
    # a 5-repeat document ~3× (peer-review Step 4).
    wall = (ceil(n_docs / SUITE_CONCURRENCY) * repeats * avg) if avg and n_docs else None
    est_tokens = (
        int(stats["avg_tokens"] * n_runs) if stats["avg_tokens"] else None
    )
    est_cost = (
        round(stats["avg_cost"] * n_runs, 2) if stats["avg_cost"] else None
    )
    return {
        "documents": n_docs,
        "repeats": repeats,
        "extraction_runs": n_runs,
        "avg_run_seconds": avg,
        "estimated_wall_seconds": wall,
        "estimated_tokens": est_tokens,
        "estimated_cost_usd": est_cost,
        "tokens_range": (
            [stats["tokens_range"][0] * n_runs, stats["tokens_range"][1] * n_runs]
            if stats["tokens_range"] else None
        ),
        "cost_range_usd": (
            [round(stats["cost_range"][0] * n_runs, 2),
             round(stats["cost_range"][1] * n_runs, 2)]
            if stats["cost_range"] else None
        ),
        "estimate_sample_size": stats["sample_size"],
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
        # Persist the RESOLVED model, never null-meaning-"whatever the
        # environment used" (Step 13) — trends compare runs by model, so the
        # stamp must state what actually ran even when the user left the
        # picker on the default.
        load_dotenv(server.ENV_FILE, override=True)
        resolved_model = body.model or os.environ.get(
            "TEST_MODEL", "openai.gpt-5.4"
        )
        launch = body.model_dump()
        launch["model"] = resolved_model
        suite_run_id = repo.create_suite_run(
            conn, suite_id=suite_id, label=body.label, config=launch,
            model=resolved_model,
        )
        # Freeze the corpus BEFORE any execution (Step 2): every queued
        # document has a durable row up front, so a doc that later fails to
        # stage is visible as failed instead of silently absent. Each doc's
        # variants are resolved HERE from its benchmark's template set
        # (Step 3) — and an explicit launch variant that contradicts a doc's
        # gold fails the whole launch fast rather than grading garbage.
        from eval.variants import benchmark_variants_for, variant_conflicts

        docs = repo.list_suite_docs(conn, suite_id)
        requested_variants = body.variants or {}
        for d in docs:
            d["source_sha256"] = _sha256_of(d.get("source_path", ""))
            try:
                derived = benchmark_variants_for(
                    conn, d.get("benchmark_id"),
                    d.get("filing_standard", "mfrs"),
                    d.get("filing_level", "company"),
                )
            except Exception:
                logger.warning("variant derivation failed for doc %s",
                               d["id"], exc_info=True)
                derived = {}
            conflicts = variant_conflicts(requested_variants, derived)
            if conflicts:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Document '{d.get('label') or d['id']}': the requested "
                        f"variant for {', '.join(conflicts)} does not match the "
                        f"variant its benchmark gold was built from. Remove the "
                        f"override or attach a matching benchmark."
                    ),
                )
            d["variants"] = derived
        repo.snapshot_suite_run_docs(conn, suite_run_id, docs)
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
        launch = sr.get("config") or {}
        # Atomic flip: only the request that wins the non-running → running
        # transition launches the batch. A read-then-write guard would let two
        # rapid Resume clicks both pass and spawn duplicate batch threads.
        cur = conn.execute(
            "UPDATE eval_suite_runs SET status = 'running' WHERE id = ? "
            "AND status != 'running'",
            (suite_run_id,),
        )
        conn.commit()
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="This suite run is already running.")
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


@router.get("/api/suites/{suite_id}/results")
async def suite_results_endpoint(suite_id: int):
    """Trend data (Step F1): every suite run of this suite with its aggregate
    scores + the config stamp (date, model, app version, label). Powers the
    Results score-trend chart."""
    from db import repository as repo
    from eval.compare import suite_run_aggregate

    conn = server._open_audit_conn()
    try:
        if repo.get_suite(conn, suite_id) is None:
            raise HTTPException(status_code=404, detail="Suite not found")
        runs = repo.list_suite_runs(conn, suite_id)
        points = []
        for sr in runs:
            agg = suite_run_aggregate(conn, sr["id"])["aggregate"]
            points.append({
                "suite_run_id": sr["id"],
                "label": sr.get("label", ""),
                "model": sr.get("model"),
                "app_version": sr.get("app_version"),
                "created_at": sr.get("created_at"),
                "status": sr.get("status"),
                "mean_accuracy": agg["mean_accuracy"],
                "mean_consistency": agg["mean_consistency"],
                "mean_cross_check_pass_rate": agg["mean_cross_check_pass_rate"],
            })
    finally:
        conn.close()
    # Oldest → newest so the trend reads left-to-right.
    points.reverse()
    return {"suite_id": suite_id, "points": points}


@router.get("/api/suites/{suite_id}/compare")
async def compare_suite_runs_endpoint(suite_id: int, a: int, b: int):
    """Compare two suite runs (Step F2): per-document accuracy deltas, aggregate
    delta, taxonomy deltas, union handling, gold-changed warning."""
    from db import repository as repo
    from eval.compare import compare_suite_runs

    conn = server._open_audit_conn()
    try:
        sr_a = repo.get_suite_run(conn, a)
        sr_b = repo.get_suite_run(conn, b)
        if sr_a is None or sr_b is None or sr_a["suite_id"] != suite_id or sr_b["suite_id"] != suite_id:
            raise HTTPException(status_code=404, detail="Suite run not found in this suite")
        result = compare_suite_runs(conn, a, b)
    finally:
        conn.close()
    return result


@router.get("/api/suites/{suite_id}/compare/slots")
async def compare_slot_diff_endpoint(
    suite_id: int, a: int, b: int, doc_id: int,
):
    """Value-level drill-down for one document across two suite runs
    (Step 12): which gold slots regressed A→B and which were fixed, with
    human line-item names. Recomputed from durable facts on demand."""
    from api.eval import resolve_slot_labels
    from db import repository as repo
    from eval.compare import _suite_run_doc_cards, slot_level_diff

    conn = server._open_audit_conn()
    try:
        sr_a = repo.get_suite_run(conn, a)
        sr_b = repo.get_suite_run(conn, b)
        if (
            sr_a is None or sr_b is None
            or sr_a["suite_id"] != suite_id or sr_b["suite_id"] != suite_id
        ):
            raise HTTPException(status_code=404, detail="Suite run not found in this suite")
        card_a = _suite_run_doc_cards(conn, a).get(doc_id)
        card_b = _suite_run_doc_cards(conn, b).get(doc_id)
        if card_a is None or card_b is None:
            raise HTTPException(
                status_code=404,
                detail="This document was not run in both suite runs.",
            )
        # Benchmark linkage: the run rows carry it (frozen at run time).
        row = conn.execute(
            "SELECT benchmark_id FROM runs WHERE id = ?", (card_a.run_id,)
        ).fetchone()
        benchmark_id = row[0] if row else None
        if benchmark_id is None:
            raise HTTPException(
                status_code=422,
                detail="This document has no benchmark — no gold to diff against.",
            )
        diff = slot_level_diff(conn, card_a.run_id, card_b.run_id, int(benchmark_id))
        resolve_slot_labels(conn, diff["regressions"] + diff["fixes"])
    finally:
        conn.close()
    return {
        "doc_id": doc_id,
        "run_id_a": card_a.run_id,
        "run_id_b": card_b.run_id,
        "benchmark_id": int(benchmark_id),
        **diff,
    }


@router.get("/api/suites/{suite_id}/runs/{suite_run_id}")
async def get_suite_run_endpoint(suite_id: int, suite_run_id: int):
    """Suite-run detail: status + per-document scorecards + the aggregate."""
    from db import repository as repo
    from eval.scorecards import aggregate_suite
    from eval.compare import _suite_run_doc_cards

    conn = server._open_audit_conn()
    try:
        sr = repo.get_suite_run(conn, suite_run_id)
        if sr is None or sr["suite_id"] != suite_id:
            raise HTTPException(status_code=404, detail="Suite run not found")
        # One scorecard per DOCUMENT (deduped across resume-retry + repeat rows),
        # so the detail view + trend/compare agree and "N of M" isn't inflated.
        cards = list(_suite_run_doc_cards(conn, suite_run_id).values())
        # The frozen corpus with per-doc execution state, so a document that
        # never produced a run (e.g. failed to stage) is still visible.
        doc_states = [
            {
                "doc_id": d["id"], "label": d.get("label", ""),
                "state": d.get("state", ""), "error": d.get("error"),
                "benchmark_id": d.get("benchmark_id"),
            }
            for d in repo.list_suite_run_docs(conn, suite_run_id)
        ]
    finally:
        conn.close()

    aggregate = aggregate_suite(cards)
    return {
        "suite_run": {k: v for k, v in sr.items() if k != "runs"},
        "documents": [c.to_dict() for c in cards],
        "doc_states": doc_states,
        "aggregate": aggregate,
    }
