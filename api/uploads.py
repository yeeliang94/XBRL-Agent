"""Upload + scout routes.

Endpoints: ``POST /api/upload``, ``POST /api/scout/{session_id}``.
Shared state/helpers are reached through ``server.X`` at call time.
"""
import asyncio
import json
import logging
import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

import server
from utils.paths import validate_session_id

logger = logging.getLogger("server")

router = APIRouter()

# Per-session scout attempt generation (peer-review HIGH, 2026-06-21). The
# scout endpoint can fire again while a cancelled scout is still unwinding (the
# common "cancel Auto-detect, re-run" flow). Because the SCOUT run_agents row is
# now reused per run (one row, not a duplicate per click), the OLD stream's
# finally must not (a) finalize the row the NEW attempt just reset to running,
# nor (b) unregister the new attempt's task under the shared "scout" key — both
# would corrupt the live attempt. Each attempt claims a monotonically-rising
# generation here (all on the single asyncio loop, so no lock is needed); the
# finally only finalizes / unregisters when it still owns the current
# generation. A superseded attempt leaves the row + registry slot to its owner.
_scout_attempt_gen: dict[str, int] = {}


def _claim_scout_attempt(session_id: str) -> int:
    """Mark a new scout attempt as the current one for this session, returning
    its generation token."""
    gen = _scout_attempt_gen.get(session_id, 0) + 1
    _scout_attempt_gen[session_id] = gen
    return gen


def _scout_attempt_is_current(session_id: str, gen: int) -> bool:
    """True if `gen` is still the latest scout attempt for the session — i.e. no
    newer attempt has taken over the shared SCOUT row / "scout" task slot."""
    return _scout_attempt_gen.get(session_id) == gen


# --- Upload endpoint ---

@router.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # Create session directory up front — we stream the upload straight to
    # disk so the full file never lives in memory (peer-review I13). A
    # running byte counter trips 413 as soon as the cap is exceeded.
    session_id = str(uuid.uuid4())
    session_dir = server.OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / "uploaded.pdf"

    _CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB reads
    total_bytes = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > server.MAX_UPLOAD_SIZE:
                    # Drain was already interrupted — discard the partial
                    # file so a half-written PDF doesn't linger.
                    out.close()
                    try:
                        target.unlink()
                        session_dir.rmdir()
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size is {server.MAX_UPLOAD_SIZE // (1024*1024)}MB.",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        # Clean up partial files on any non-HTTP error.
        try:
            if target.exists():
                target.unlink()
            session_dir.rmdir()
        except OSError:
            pass
        raise

    # Persist the ORIGINAL filename as a sidecar so History can show a
    # meaningful name later. The file on disk is always "uploaded.pdf" to
    # keep downstream paths simple; the sidecar is the single source of
    # truth for the user-facing name.
    # UTF-8 encoding is mandatory — Windows defaults to charmap and will
    # crash on non-ASCII filenames (see CLAUDE.md issue #1).
    (session_dir / "original_filename.txt").write_text(
        file.filename, encoding="utf-8"
    )

    # Persistent-draft contract (PLAN-persistent-draft-uploads.md): the upload
    # response carries a `run_id` so the frontend can navigate to a shareable
    # `/run/{run_id}` URL immediately. The draft row holds the user-visible
    # filename + session pointer; config is filled in via PATCH as the user
    # picks statements / level / standard / model in PreRunPanel.
    #
    # Best-effort: if the audit DB is unhappy, we still return the upload
    # response so the UI flow is not blocked. The user gets a non-persistent
    # session (legacy behaviour); History just won't see this draft. Logs
    # capture the failure for ops.
    run_id: Optional[int] = None
    try:
        import sqlite3
        from db import repository as repo
        # `init_db` already runs once in the lifespan handler — see the
        # comment at `_lifespan` for the per-request-cost rationale
        # (peer-review #9). Don't re-run it here.
        db_conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
        try:
            db_conn.execute("PRAGMA foreign_keys = ON")
            db_conn.execute("PRAGMA busy_timeout = 5000")
            run_id = repo.create_run(
                db_conn,
                pdf_filename=file.filename,
                session_id=session_id,
                output_dir=str(session_dir),
                config=None,
                scout_enabled=False,
                status="draft",
            )
            db_conn.commit()
        finally:
            db_conn.close()
    except Exception:
        logger.exception(
            "Failed to insert draft runs row for session %s", session_id,
        )

    return {"session_id": session_id, "filename": file.filename, "run_id": run_id}


# --- Scout endpoint (Phase 7.1) ---

@router.post("/api/scout/{session_id}")
async def scout_pdf(session_id: str, request: Request):
    """Run the scout agent on an uploaded PDF and stream progress via SSE.

    Returns an SSE stream with status events during processing, then a
    final 'scout_complete' event containing the full infopack JSON.

    Accepts an optional JSON body ``{"scanned_pdf": bool}`` — when true,
    the scout's notes-inventory tool skips the PyMuPDF-regex fast path
    and runs the vision pass directly. Use this when the operator knows
    the uploaded PDF is image-only.
    """
    try:
        validate_session_id(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id.")
    session_dir = server.OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    # Body is optional — absent/empty/non-JSON all mean "default behaviour".
    # We don't fail the request on malformed JSON because the old callers
    # (Phase 7.1 UI) post no body at all.
    force_vision_inventory = False
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict) and body.get("scanned_pdf") is True:
        force_vision_inventory = True

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    global_model = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) must be set. Check Settings.")

    # Resolve scout model from per-agent settings (Phase 8), falling back to
    # the global model if no scout-specific override has been configured.
    extended = server._load_extended_settings()
    scout_model_name = extended["default_models"].get("scout", global_model)

    # Build a provider-backed model so the scout uses the same proxy/direct
    # wiring as extraction agents (critical for enterprise proxy on Windows).
    scout_model = server._create_proxy_model(scout_model_name, proxy_url, api_key)

    # Item 2 (PLAN-orchestration-hardening): give the scout an audit
    # presence. The draft `runs` row already exists from upload time, so we
    # can (a) thread the session dir in as the trace destination and
    # (b) create a SCOUT run_agents row — which is what opens the
    # `GET /api/runs/{id}/agents/SCOUT/trace` whitelist gate (api/runs.py
    # rejects statements with no run_agents row BEFORE the path check).
    # All best-effort: an audit-DB hiccup must never block the scout.
    def _resolve_scout_run_id() -> Optional[int]:
        try:
            import sqlite3
            conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
            try:
                row = conn.execute(
                    "SELECT id FROM runs WHERE session_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                return int(row[0]) if row else None
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not resolve run for scout session %s", session_id,
                exc_info=True,
            )
            return None

    def _record_scout_agent_row(run_id: int) -> Optional[int]:
        try:
            import sqlite3
            from db import repository as repo
            conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
            try:
                # Idempotent per run: a re-scout (e.g. cancel Auto-detect
                # midway, then re-run) reuses the existing SCOUT row instead
                # of inserting a second one — otherwise History shows two
                # SCOUT cards that both resolve to the single overwritten
                # SCOUT_conversation_trace.json (the "two scout traces" bug).
                agent_row_id = repo.reset_or_create_scout_agent_row(
                    conn, run_id, model=scout_model_name,
                )
                conn.commit()
                return agent_row_id
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not create SCOUT run_agents row for run %s", run_id,
                exc_info=True,
            )
            return None

    def _finish_scout_agent_row(
        agent_row_id: int, status: str, error: str = "scout failed",
    ) -> None:
        try:
            import sqlite3
            from db import repository as repo
            conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
            try:
                repo.finish_run_agent(
                    conn, agent_row_id, status,
                    # v17 (item 9): SCOUT rows carry the failure class too.
                    # Pass the real failure detail (e.g. the scout timeout
                    # reason) so a degraded scout derives turn_timeout /
                    # wallclock rather than a generic class.
                    error_type=server._agent_row_error_type(
                        status, None, error),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not finish SCOUT run_agents row %s", agent_row_id,
                exc_info=True,
            )

    async def scout_stream():
        import asyncio
        import task_registry

        # Queue for structured events from the streaming scout agent
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def on_event(event_type: str, data: dict) -> None:
            await event_queue.put((event_type, data))

        scout_task: Optional[asyncio.Task] = None
        scout_run_id = _resolve_scout_run_id()
        # Claim this attempt's generation IMMEDIATELY after (re)setting the
        # shared SCOUT row, before the first await — so a still-unwinding older
        # attempt sees it's been superseded and skips its finalize/unregister.
        scout_agent_row_id = (
            _record_scout_agent_row(scout_run_id)
            if scout_run_id is not None else None
        )
        scout_attempt = _claim_scout_attempt(session_id)
        scout_row_status = "failed"
        scout_error_detail = "scout failed"
        try:
            yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': 'Starting scout...'})}\n\n"

            from scout.runner import run_scout_streaming
            scout_task = asyncio.create_task(run_scout_streaming(
                pdf_path=pdf_path,
                model=scout_model,
                on_event=on_event,
                force_vision_inventory=force_vision_inventory,
                output_dir=str(session_dir),
            ))

            # Register so abort endpoints can cancel it
            task_registry.register(session_id, "scout", scout_task)

            # Forward structured events as SSE while scout runs
            while not scout_task.done():
                try:
                    event_type, data = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining events
            while not event_queue.empty():
                event_type, data = event_queue.get_nowait()
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

            infopack = scout_task.result()

            # to_json() returns a JSON string; parse it to embed as a nested dict
            infopack_dict = json.loads(infopack.to_json())

            # Honesty (Codex review): a scout that hit its per-turn or
            # wall-clock timeout returns a degraded (often empty) pack. It
            # must NOT be reported as a successful scout — the agent already
            # emitted a `scout_timeout` error, so a `success: true` completion
            # would contradict it and overwrite the timeout signal. Mark the
            # audit row failed (deriving turn_timeout / wallclock from the
            # reason) and emit `scout_complete success:false`. The run can
            # still proceed without hints (gotcha #13) — only the reporting
            # is corrected.
            if getattr(infopack, "degraded", False):
                scout_row_status = "failed"
                scout_error_detail = (
                    getattr(infopack, "degraded_reason", None)
                    or "Scout degraded before completing."
                )
                payload = {
                    "success": False,
                    "degraded": True,
                    "message": scout_error_detail,
                    "infopack": infopack_dict,
                }
                yield f"event: scout_complete\ndata: {json.dumps(payload)}\n\n"
            else:
                scout_row_status = "succeeded"
                yield f"event: scout_complete\ndata: {json.dumps({'success': True, 'infopack': infopack_dict})}\n\n"

        except asyncio.CancelledError:
            scout_row_status = "cancelled"
            logger.info("Scout cancelled by user", extra={"session_id": session_id})
            yield f"event: scout_cancelled\ndata: {json.dumps({'message': 'Scout cancelled by user'})}\n\n"

        except Exception as e:
            # The full traceback is logged server-side only. It is NOT sent to
            # the browser: a provider/LLM stack trace can embed request
            # internals or credentials (mirrors the generic-response policy in
            # /api/test-connection). The client gets the exception summary.
            logger.exception("Scout failed", extra={"session_id": session_id})
            scout_error_detail = str(e) or "scout failed"
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        finally:
            # Cancel THIS attempt's task if still running (e.g. client
            # disconnected). Always safe — we only ever cancel our own task.
            if scout_task is not None and not scout_task.done():
                scout_task.cancel()
                try:
                    await scout_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Only finalize the row + release the registry slot if we still own
            # the current attempt. If a newer scout took over while we were
            # unwinding, it now owns the (reused) row and the "scout" task slot
            # — touching them here would mark the live attempt cancelled and
            # unregister its task, breaking Stop (peer-review HIGH).
            if _scout_attempt_is_current(session_id, scout_attempt):
                task_registry.unregister(session_id, "scout")
                # Item 2: finalize the SCOUT audit row so the trace route's
                # whitelist gate stays open and History/Telemetry can list it.
                if scout_agent_row_id is not None:
                    _finish_scout_agent_row(
                        scout_agent_row_id, scout_row_status, scout_error_detail,
                    )

    return StreamingResponse(
        scout_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
