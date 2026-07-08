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
    # Validate a client-supplied override the same way Settings does — a bad
    # value would otherwise pick an arbitrary (possibly priciest) provider/model
    # straight into _create_proxy_model.
    if isinstance(override, str) and override.strip():
        override = override.strip()
        if len(override) > 128:
            raise HTTPException(
                status_code=422, detail="model override exceeds 128 characters.",
            )
        # Unknown id is a soft warning, not an error — mirrors the Settings
        # default_models path: the registry may have been edited without a
        # restart, or a new model added but not yet loaded. The length cap
        # above already bounds abuse.
        known_model_ids = {m["id"] for m in server._load_available_models() if "id" in m}
        if known_model_ids and override not in known_model_ids:
            logger.warning(
                "re-review model override %r not in config/models.json", override,
            )
    else:
        override = None
    # Bound free-text guidance — it's folded verbatim into the reviewer prompt,
    # so an unbounded value is a cost / prompt-stuffing vector (matches the
    # human_answer cap on the flag-answer endpoint).
    guidance_in = (body or {}).get("guidance") if isinstance(body, dict) else None
    if isinstance(guidance_in, str) and len(guidance_in) > 8000:
        raise HTTPException(
            status_code=422, detail="guidance exceeds 8000 characters.",
        )
    model_name = (
        override
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

    def _ensure_correction_agent_row() -> Optional[int]:
        """Reuse-or-create the run's CORRECTION ``run_agents`` row.

        ``_run_reviewer_pass`` saves its transcript as
        ``CORRECTION_conversation_trace.json``, but the trace route whitelists
        by ``run_agents.statement_type`` — so a manual re-review on a run that
        never auto-reviewed had no row and its trace 404'd (peer-review
        MEDIUM). The auto-review path creates this row inline; mirror it here.
        Reuse an existing row (re-opened to ``running``) so repeated
        re-reviews don't churn duplicate audit rows. Best-effort — a
        telemetry-row failure must never block the pass itself.
        """
        from db import repository as repo
        try:
            conn = server._open_audit_conn()
            try:
                existing = conn.execute(
                    "SELECT id FROM run_agents WHERE run_id = ? AND "
                    "statement_type = ? ORDER BY id DESC LIMIT 1",
                    (run_id, server.CORRECTION_AGENT_ID),
                ).fetchone()
                if existing is not None:
                    rid = int(existing[0])
                    conn.execute(
                        "UPDATE run_agents SET status = 'running', model = ? "
                        "WHERE id = ?",
                        (model_name, rid),
                    )
                    conn.commit()
                    return rid
                rid = repo.create_run_agent(
                    conn, run_id,
                    statement_type=server.CORRECTION_AGENT_ID,
                    variant=None, model=model_name,
                )
                conn.commit()
                return rid
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — trace reachability is best-effort
            logger.warning(
                "could not ensure CORRECTION run_agent row for run %s",
                run_id, exc_info=True,
            )
            return None

    def _finalize_correction_agent_row(agent_id: Optional[int], outcome: dict) -> None:
        """Close the CORRECTION row, mirroring the auto path's status logic."""
        if agent_id is None:
            return
        from db import repository as repo
        try:
            conn = server._open_audit_conn()
            try:
                status = "failed" if outcome.get("error") else "completed"
                repo.finish_run_agent(
                    conn, agent_id, status=status, workbook_path=None,
                    total_tokens=int(outcome.get("total_tokens", 0) or 0),
                    total_cost=float(outcome.get("total_cost", 0.0) or 0.0),
                    turn_count=int(outcome.get("turns_used", 0) or 0),
                    # Run-168 QA fix: same rollups the auto path persists —
                    # without them the Activity row reads "0 tool calls".
                    prompt_tokens=int(outcome.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(
                        outcome.get("completion_tokens", 0) or 0),
                    tool_call_count=int(
                        outcome.get("tool_call_count", 0) or 0),
                    # v17 (item 9): classify the manual re-review outcome.
                    error_type=server._error_type_for_outcome(
                        outcome.get("error")),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "could not finalize CORRECTION run_agent row for run %s",
                run_id, exc_info=True,
            )

    async def _runner_async() -> dict:
        # Build the model inside this loop so its async HTTP client binds here.
        model = server._create_proxy_model(model_name, proxy_url, api_key)
        failed, conflicts, combined, pdf_path = _gather()
        correction_agent_id = _ensure_correction_agent_row()
        # Sentinel error: if _run_reviewer_pass RAISES (vs returning an error
        # outcome), the finally below must still close the CORRECTION row as
        # failed with a non-null v17 error_type — a bare {} would close it
        # status="completed"/error_type=NULL while run_review_tasks records
        # ok:false (split-brain). The real return overwrites this.
        outcome: dict = {"error": "reviewer_pass_raised"}
        try:
            outcome = await server._run_reviewer_pass(
                failed_checks=failed, conflicts=conflicts, model=model,
                filing_level=filing_level, filing_standard=filing_standard,
                event_queue=None,
                db_path=server.AUDIT_DB_PATH, run_id=run_id, pdf_path=pdf_path,
                guidance=combined,
            )
            if outcome.get("writes_performed", 0) > 0:
                # Item 12: capture the re-export result. _reexport_remerge_durable
                # returns False when run_concept_facts moved but filled.xlsx did
                # NOT — the Review-tab diff (DB-fed) is then correct while the
                # download is stale. Flag it on the outcome (rides into
                # run_review_tasks.outcome_json) so the Review tab can show a
                # "download may be stale" badge instead of a silent divergence.
                if not server._reexport_remerge_durable(run_id):
                    outcome["export_stale"] = True
                # The reviewer changed facts — refresh the persisted cross-checks
                # so the Review tab and any later re-review see current pass/fail
                # state, not the pre-fix failures (peer-review P1), then safely
                # downgrade the run badge if failures remain.
                server._refresh_persisted_cross_checks(run_id)
                server._safe_downgrade_run_status(run_id)
            return outcome
        finally:
            # Always close the row — even if the pass raised — so the trace is
            # reachable and the row never lingers in `running`.
            _finalize_correction_agent_row(correction_agent_id, outcome)

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
    # Item 11: `out` carries cascade_ok / cascade_error — the facts ARE
    # restored (200), but if the post-revert recompute failed the response
    # rides the warning to the Review tab instead of a silent stale-totals
    # window. Spread it verbatim.
    return {"ok": True, **out}
