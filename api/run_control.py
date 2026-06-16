"""Run-execution control routes.

Endpoints:
  ``POST /api/run/{session_id}``           — start extraction (legacy path)
  ``POST /api/runs/{run_id}/start``        — start a persistent-draft run
  ``POST /api/abort/{session_id}``         — cancel all agents
  ``POST /api/abort/{session_id}/{agent}`` — cancel one agent
  ``POST /api/runs/{run_id}/rerun-notes``  — regenerate notes sheets
  ``POST /api/rerun/{session_id}``         — re-run a single agent

These wrap ``server.run_multi_agent_stream`` (which Phase 5.2 turns into the
explicit phase pipeline). Shared state/helpers are read through ``server.X``
at call time; ``RunConfigRequest`` is a stable model so it's imported directly.
"""
import json
import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

import server
from server import RunConfigRequest
from utils.paths import validate_session_id

logger = logging.getLogger("server")

router = APIRouter()


@router.post("/api/run/{session_id}")
async def run_multi_extraction(session_id: str, body: RunConfigRequest):
    """Multi-agent SSE endpoint — runs extraction for multiple statements.

    Accepts a RunConfig body specifying which statements to extract,
    their variants, optional model overrides, and optional infopack
    from a prior scout run.
    """
    try:
        validate_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id.")
    session_dir = server.OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    # Reserve the session BEFORE returning StreamingResponse. If we only
    # reserved inside the generator (as we did pre-fix for I4), two
    # concurrent requests could both pass the `in active_runs` check
    # before either generator started — allowing parallel extractions
    # against the same session directory. The async generator's finally
    # releases the reservation on every exit path (normal completion,
    # exception, client disconnect, garbage-collection close), so the
    # I4 leak on never-started streams is still covered.
    if session_id in server.active_runs:
        raise HTTPException(status_code=409, detail="Extraction already running for this session.")
    server.active_runs.add(session_id)

    try:
        load_dotenv(server.ENV_FILE, override=True)
        api_key = server._resolve_api_key()
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) must be set. Check Settings.",
            )

        async def event_stream():
            try:
                async for evt in server.run_multi_agent_stream(
                    session_id=session_id,
                    session_dir=session_dir,
                    run_config=body,
                    api_key=api_key,
                    proxy_url=proxy_url,
                    model_name=model_name,
                ):
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
            finally:
                server.active_runs.discard(session_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    except Exception:
        # Anything that prevents the StreamingResponse from being returned
        # (e.g. missing API key HTTPException) must release the reservation
        # we just acquired, or the session would stay locked until restart.
        server.active_runs.discard(session_id)
        raise


## Legacy GET /api/run/{session_id} endpoint was removed in Phase 11.3.
## Use POST /api/run/{session_id} with RunConfigRequest body instead.


@router.post("/api/runs/{run_id}/start")
async def start_run_endpoint(run_id: int):
    """Start an extraction for a draft run created via the upload endpoint.

    Persistent-draft contract (PLAN-persistent-draft-uploads.md, Phase B):
    POST /api/upload created a draft row with status='draft'; the frontend
    PATCHed config onto it; clicking "Start" hits this endpoint, which
    flips draft → running and streams the same SSE the legacy
    POST /api/run/{session_id} would have. The legacy endpoint stays alive
    so CLI/Windows clients keep working unchanged.

    Validation order (matches the legacy path so error semantics are
    consistent):
      1. Run exists → else 404.
      2. Status is 'draft' → else 409 (don't restart a running/completed
         row).
      3. Stored config parses as RunConfigRequest with at least one
         statement → else 422.
      4. PDF file exists on disk → else 404 (the upload sidecar is the
         single source of truth for the on-disk PDF).
      5. API key + active_runs reservation, identical to the legacy path.
    """
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run is not a draft (current status: {run.status}). "
                "Only drafts can be started."
            ),
        )

    # Parse the persisted config back into a RunConfigRequest so the same
    # validation rules apply at start time as the legacy POST body. A draft
    # with NULL config or an empty `statements` list trips a 422 here.
    raw_config = run.config or {}
    try:
        run_config = RunConfigRequest(**raw_config)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if not run_config.statements:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["body", "statements"],
                    "msg": "Pick at least one statement before starting the run.",
                    "type": "value_error.missing",
                }
            ],
        )

    session_id = run.session_id
    session_dir = server.OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail="PDF not found on disk. Re-upload the file.",
        )

    if session_id in server.active_runs:
        raise HTTPException(
            status_code=409,
            detail="Extraction already running for this session.",
        )

    # Atomic flip BEFORE we acquire the active_runs reservation OR open
    # the SSE stream (peer-review HIGH #3). The atomic UPDATE is the
    # single source of truth for "this thread won the right to start
    # this draft" — if it returns rowcount=0 another request raced us
    # and we must NOT proceed (silently creating a fresh row would
    # break the shareable /run/{id} URL semantics). Doing the flip
    # here also lets `run_multi_agent_stream`'s `existing_run_id` path
    # trust the row is already 'running'.
    flip_conn = server._open_audit_conn()
    try:
        flipped = repo.mark_draft_started(flip_conn, run_id)
        flip_conn.commit()
    finally:
        flip_conn.close()
    if not flipped:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run is not a draft (state changed during the request). "
                "Only drafts can be started."
            ),
        )

    server.active_runs.add(session_id)

    try:
        load_dotenv(server.ENV_FILE, override=True)
        api_key = server._resolve_api_key()
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) "
                    "must be set. Check Settings."
                ),
            )

        async def event_stream():
            try:
                async for evt in server.run_multi_agent_stream(
                    session_id=session_id,
                    session_dir=session_dir,
                    run_config=run_config,
                    api_key=api_key,
                    proxy_url=proxy_url,
                    model_name=model_name,
                    existing_run_id=run_id,
                ):
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
            finally:
                server.active_runs.discard(session_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        # Anything that prevents the StreamingResponse from being returned
        # MUST roll the row back to 'draft' AND release the active_runs
        # reservation; otherwise the user sees a 5xx but the row is stuck
        # 'running' forever. The flip rollback is best-effort — if it
        # fails the row is no worse off than today's legacy path on the
        # same kind of failure.
        server.active_runs.discard(session_id)
        try:
            rb = server._open_audit_conn()
            try:
                rb.execute(
                    "UPDATE runs SET status='draft', started_at='' "
                    "WHERE id=? AND status='running'",
                    (run_id,),
                )
                rb.commit()
            finally:
                rb.close()
        except Exception:
            logger.warning(
                "Failed to roll draft %s back to draft after start error",
                run_id, exc_info=True,
            )
        raise


# ---------------------------------------------------------------------------
# Abort endpoints — cancel running agents without restarting
# ---------------------------------------------------------------------------

@router.post("/api/abort/{session_id}")
async def abort_session(session_id: str):
    """Cancel ALL running agents for a session."""
    import task_registry
    count = task_registry.cancel_all(session_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="No active tasks for this session.")
    return {"cancelled": count}


@router.post("/api/abort/{session_id}/{agent_id}")
async def abort_agent(session_id: str, agent_id: str):
    """Cancel a single agent within a session (e.g. abort just SOPL)."""
    import task_registry
    if not task_registry.cancel_agent(session_id, agent_id):
        raise HTTPException(status_code=404, detail="Agent not found or already finished.")
    return {"cancelled": agent_id}


# ---------------------------------------------------------------------------
# Rerun endpoint — re-extract a single statement in an existing session
# ---------------------------------------------------------------------------

@router.post("/api/runs/{run_id}/rerun-notes")
async def rerun_notes(run_id: int):
    """Regenerate the notes sheets for a completed run.

    Peer-review [HIGH] #1: before this endpoint existed, the
    Regenerate-notes button on the History-page run detail redirected
    to `/?session=<id>#notes` — a URL no code consumed. Users clicking
    it landed on the Extract page with no Rerun affordance (that button
    only shows for failed/cancelled agents). This endpoint is the real
    target: it reads the run's session + config from the DB, builds a
    notes-only `RunConfigRequest` server-side, and delegates to the same
    `run_multi_agent_stream` the per-agent rerun uses.

    Keeping the config build server-side (instead of expecting the
    frontend to reconstruct a RunConfigRequest from the run detail
    payload) means the Regenerate flow stays resilient to new
    RunConfigRequest fields landing in the future — only this endpoint
    needs to learn about them.
    """
    # Look up the run row — we need its session_id (for active-runs
    # locking + output dir) and its stored `run_config_json`.
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        from db.repository import fetch_run as _fetch_run
        run = _fetch_run(conn, run_id)
    finally:
        conn.close()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

    config = run.config or {}
    notes_to_run = config.get("notes_to_run") or []
    if not notes_to_run:
        raise HTTPException(
            status_code=400,
            detail=(
                "This run has no notes templates in its config — nothing "
                "to regenerate. Run the notes pipeline on a fresh session "
                "instead."
            ),
        )

    session_id = run.session_id
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} has no session_id — legacy row can't be rerun.",
        )

    if session_id in server.active_runs:
        raise HTTPException(
            status_code=409,
            detail="Extraction still running for this session. Wait for it to finish before regenerating.",
        )

    session_dir = server.OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail="PDF not found for this session — cannot regenerate.",
        )

    # Build a notes-only RunConfigRequest from the stored config. Clear
    # `statements` so only the notes coordinator runs; preserve
    # filing_level, filing_standard, infopack, use_scout, and any
    # per-template model overrides so the regenerated notes match the
    # original run's environment.
    try:
        regen_config = RunConfigRequest(
            statements=[],
            variants={},
            models={},
            infopack=config.get("infopack"),
            use_scout=False,  # no new scout pass — reuse stored infopack
            filing_level=config.get("filing_level", "company"),
            filing_standard=config.get("filing_standard", "mfrs"),
            notes_to_run=list(notes_to_run),
            notes_models=config.get("notes_models") or {},
        )
    except Exception as e:
        # Malformed stored config — surface rather than crash mid-stream.
        raise HTTPException(
            status_code=400,
            detail=f"Stored run config is malformed: {e}",
        )

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="API key not set. Check Settings.",
        )

    server.active_runs.add(session_id)

    async def event_stream():
        try:
            async for evt in server.run_multi_agent_stream(
                session_id=session_id,
                session_dir=session_dir,
                run_config=regen_config,
                api_key=api_key,
                proxy_url=proxy_url,
                model_name=model_name,
            ):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        finally:
            server.active_runs.discard(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/rerun/{session_id}")
async def rerun_agent(session_id: str, body: RunConfigRequest):
    """Re-run extraction for a single agent within an existing session.

    Accepts either exactly one face statement OR exactly one notes template,
    never both — rerun is a targeted retry for one failed/cancelled agent.
    Reuses the same output directory so the new workbook overwrites the old
    one. After the agent finishes, merge + cross-checks run against all
    workbooks in the session (both old successful ones and the new one).
    """
    try:
        validate_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id.")

    n_stmts = len(body.statements)
    n_notes = len(body.notes_to_run)
    if n_stmts + n_notes != 1:
        raise HTTPException(
            status_code=400,
            detail="Rerun expects exactly one statement or one notes template.",
        )

    # Block rerun while an extraction is already running for this session
    if session_id in server.active_runs:
        raise HTTPException(status_code=409, detail="Extraction still running. Wait for it to finish before rerunning.")

    session_dir = server.OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found for this session.")

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

    if not api_key:
        raise HTTPException(status_code=400, detail="API key not set. Check Settings.")

    server.active_runs.add(session_id)

    async def event_stream():
        try:
            async for evt in server.run_multi_agent_stream(
                session_id=session_id,
                session_dir=session_dir,
                run_config=body,
                api_key=api_key,
                proxy_url=proxy_url,
                model_name=model_name,
            ):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        finally:
            server.active_runs.discard(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
