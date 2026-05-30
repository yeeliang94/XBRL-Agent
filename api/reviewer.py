"""Reviewer-tab orchestration routes (docs/Archive/PLAN-reviewer-agent.md, Steps 13-14).

The read surface (GET /review, POST /flags/{id}/answer) lives in
concept_model/reviewer_routes.py; these three need server orchestration
(model creation, the reviewer pass, workbook re-export), so they read shared
helpers through ``server.X`` at call time.

Endpoints:
  ``POST /api/runs/{run_id}/re-review``          — launch a background reviewer pass
  ``GET  /api/runs/{run_id}/re-review/status``   — poll the latest pass
  ``POST /api/runs/{run_id}/revert-to-original`` — restore the original snapshot
"""
import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

import server

logger = logging.getLogger("server")

router = APIRouter()


@router.post("/api/runs/{run_id}/re-review")
async def re_review(run_id: int, body: Optional[dict] = None):
    """Launch a reviewer pass over the run's CURRENT facts in the background.

    Returns ``{ok, status: "running", model}`` immediately; the heavy pass
    (LLM turns + workbook re-export) runs as a tracked background task. The
    Review tab polls :func:`re_review_status` for the result. A pass already
    running for the run is reported back rather than double-launched.

    Optional free-text ``guidance`` plus every active flag's context is folded
    into the review packet. The original snapshot is preserved
    (``_run_reviewer_pass`` only snapshots when none exists yet), so a later
    revert still goes back to the first extraction.
    """
    from db import repository as repo
    from types import SimpleNamespace
    from correction.reviewer_agent import load_open_conflicts

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    config = run.config or {}
    filing_level = config.get("filing_level", "company")
    filing_standard = config.get("filing_standard", "mfrs")

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    # Model precedence: explicit per-request override (the Review-tab picker)
    # → the configured reviewer default → the run's model → TEST_MODEL.
    override = (body or {}).get("model") if isinstance(body, dict) else None
    model_name = (
        (override if isinstance(override, str) and override else None)
        or server._reviewer_model_name()
        or config.get("model")
        or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    )
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not set. Check Settings.")

    # Re-entrancy guard: never run two reviewer passes over the same run at
    # once (they'd race the same facts + snapshot). Report the in-flight pass
    # instead of launching a second.
    from db import repository as repo
    existing_conn = server._open_audit_conn()
    try:
        existing = repo.fetch_review_task(existing_conn, run_id)
    finally:
        existing_conn.close()
    if existing and existing.get("status") == "running":
        return {
            "ok": True, "status": "running", "already_running": True,
            "model": existing.get("model_name"),
        }

    # Everything heavy — building the model, gathering the review packet,
    # running the pass, and re-exporting — runs on a dedicated thread with its
    # OWN event loop (asyncio.run). This isolates a minutes-long pass from the
    # request loop: it can't be cancelled by request teardown (a raw
    # create_task is), can't block other requests, and keeps the model's async
    # HTTP client bound to the loop that actually uses it.
    #
    # The initial 'running' persist is MANDATORY (peer-review MEDIUM), not
    # best-effort: the re-entrancy guard above reads this row to refuse a
    # duplicate pass, so a swallowed failure would let a second POST launch a
    # second reviewer over the same facts. Write it DIRECTLY (not through the
    # swallowing `_save_review_task`) and 503 on failure — and do NOT start the
    # background thread unless the row is durable. The terminal 'done' write
    # later CAN stay best-effort: by then the thread owns the state and a lost
    # telemetry write is reconciled at startup.
    launch_conn = server._open_audit_conn()
    try:
        repo.upsert_review_task(
            launch_conn, run_id, "running", model_name=model_name
        )
        launch_conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "re-review launch persist failed for run %s", run_id, exc_info=True
        )
        raise HTTPException(
            status_code=503,
            detail="Could not record the re-review launch; please try again.",
        ) from e
    finally:
        launch_conn.close()

    def _gather():
        # Failing cross-checks come from the run's STORED cross_checks rows —
        # the exact set the user sees on the Cross-checks / Review tab. Do NOT
        # re-derive them via _recheck_from_facts: that re-exports only
        # *succeeded* statements, so a check that failed because its statement
        # failed to extract (e.g. sopl_to_socie_profit when the SOPL agent
        # errored) silently becomes not-applicable and the reviewer would see
        # "nothing to review" even though a failure is plainly listed (run
        # #146). Reading the authoritative table keeps the reviewer's input
        # identical to the user's view.
        conn2 = server._open_audit_conn()
        try:
            rows = conn2.execute(
                "SELECT check_name, expected, actual, diff, message, "
                "target_sheet, target_row, comparands_json FROM cross_checks "
                "WHERE run_id = ? AND status = 'failed' ORDER BY id",
                (run_id,),
            ).fetchall()
        finally:
            conn2.close()
        # Decode the persisted comparands (Phase 2) so a manual re-review gets
        # the same entry points the inline auto-review had from live objects.
        from cross_checks.framework import comparands_from_json
        failed = [
            SimpleNamespace(
                name=r["check_name"], expected=r["expected"], actual=r["actual"],
                diff=r["diff"], message=r["message"],
                target_sheet=r["target_sheet"], target_row=r["target_row"],
                comparands=comparands_from_json(
                    r["comparands_json"]
                    if "comparands_json" in r.keys() else None
                ),
            )
            for r in rows
        ]
        conflicts = [
            c for c in load_open_conflicts(server.AUDIT_DB_PATH, run_id)
            if c.get("kind") != "correction_exhausted"
        ]
        user_guidance = (
            (body or {}).get("guidance") if isinstance(body, dict) else None
        )
        active = server._active_flag_guidance(run_id)
        combined = "\n\n".join(
            g for g in (user_guidance, active) if g and g.strip()
        )
        # Resolve the source PDF from the run's output dir first — the merged
        # workbook may be absent on a run that failed before merge, which is
        # exactly the kind of run most in need of re-review. Without the PDF
        # the grounding guard would block every non-arithmetic fix. Fall back
        # to the merged workbook's parent for older rows predating output_dir.
        pdf_path = None
        for base in (run.output_dir, getattr(run, "merged_workbook_path", None)):
            if not base:
                continue
            candidate = (
                Path(base) / "uploaded.pdf" if base == run.output_dir
                else Path(base).parent / "uploaded.pdf"
            )
            if candidate.exists():
                pdf_path = str(candidate)
                break
        return failed, conflicts, combined or None, pdf_path

    async def _runner_async() -> dict:
        # Build the model inside this loop so its async HTTP client binds here.
        model = server._create_proxy_model(model_name, proxy_url, api_key)
        failed, conflicts, combined, pdf_path = _gather()
        outcome = await server._run_reviewer_pass(
            failed_checks=failed, conflicts=conflicts, model=model,
            filing_level=filing_level, filing_standard=filing_standard,
            event_queue=None,
            db_path=server.AUDIT_DB_PATH, run_id=run_id, pdf_path=pdf_path,
            guidance=combined,
        )
        if outcome.get("writes_performed", 0) > 0:
            server._reexport_remerge_durable(run_id)
            # The reviewer changed facts — refresh the persisted cross-checks
            # so the Review tab and any later re-review see current pass/fail
            # state, not the pre-fix failures (peer-review P1), then safely
            # downgrade the run badge if failures remain.
            server._refresh_persisted_cross_checks(run_id)
            server._safe_downgrade_run_status(run_id)
        return outcome

    def _thread_main() -> None:
        try:
            outcome = asyncio.run(_runner_async())
            # Surface reviewer failures honestly: a snapshot/construction
            # failure, exhaustion, or wall-clock timeout sets
            # ``outcome["error"]``. ``ok`` reflects the pass outcome (not merely
            # "the request ran") so the UI never shows a phantom success
            # (peer-review HIGH). ``model`` echoes which model ran.
            result = {
                "ok": not outcome.get("error"), "model": model_name, **outcome,
            }
        except Exception as e:  # noqa: BLE001 — record, never lose the thread
            logger.exception("background re-review failed for run %s", run_id)
            result = {
                "ok": False, "model": model_name,
                "error": f"{type(e).__name__}: {e}",
            }
        # Persist the terminal outcome so a poll (even after a restart) reads
        # it. _save_review_task is itself best-effort, so this never re-raises.
        server._save_review_task(run_id, "done", model_name=model_name,
                                 outcome=result)

    threading.Thread(
        target=_thread_main, name=f"re-review-{run_id}", daemon=True,
    ).start()
    return {"ok": True, "status": "running", "model": model_name}


@router.get("/api/runs/{run_id}/re-review/status")
async def re_review_status(run_id: int):
    """Poll the latest manual re-review pass for ``run_id``.

    ``idle`` — no pass has been launched (or the process restarted).
    ``running`` — a pass is in flight; keep polling.
    ``done`` — the pass finished; the body carries the same outcome the
    synchronous endpoint used to return (``ok``, ``model``, ``invoked``,
    ``writes_performed``, ``flags_raised``, ``error``).
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        state = repo.fetch_review_task(conn, run_id)
    finally:
        conn.close()
    if state is None:
        return {"status": "idle"}
    if state.get("status") == "running":
        return {"status": "running", "model": state.get("model_name")}
    return {"status": "done", **(state.get("outcome") or {})}


@router.post("/api/runs/{run_id}/revert-to-original")
async def revert_to_original_endpoint(run_id: int):
    """Restore the run's facts from the original snapshot (Step 14).

    Calls the versioning revert (which also dismisses reviewer flags and
    recomputes totals), then re-exports + re-merges so the download equals
    the original extraction. The revert (DB + cascade) and the heavy openpyxl
    re-export both run off the event loop via ``asyncio.to_thread`` so a large
    workbook can't block other requests (mirrors the re-review path).
    """
    from db import repository as repo
    from concept_model.versioning import revert_to_original

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    out = await asyncio.to_thread(revert_to_original, server.AUDIT_DB_PATH, run_id)
    if not out.get("reverted"):
        raise HTTPException(
            status_code=409,
            detail="No reviewer version exists for this run — nothing to revert.",
        )
    await asyncio.to_thread(server._reexport_remerge_durable, run_id)
    # Facts are back to the original (pre-reviewer) state — refresh the
    # persisted cross-checks so the Review/Cross-checks tabs don't keep showing
    # the reviewer's post-fix (e.g. passed) results against restored facts that
    # may fail again (peer-review P1), then safely downgrade the run badge if
    # the restored facts re-introduce failures.
    await asyncio.to_thread(server._refresh_persisted_cross_checks, run_id)
    await asyncio.to_thread(server._safe_downgrade_run_status, run_id)
    return {"ok": True, **out}
