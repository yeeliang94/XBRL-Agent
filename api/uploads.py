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

logger = logging.getLogger("server")

router = APIRouter()


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

    async def scout_stream():
        import asyncio
        import task_registry

        # Queue for structured events from the streaming scout agent
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def on_event(event_type: str, data: dict) -> None:
            await event_queue.put((event_type, data))

        scout_task: Optional[asyncio.Task] = None
        try:
            yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': 'Starting scout...'})}\n\n"

            from scout.runner import run_scout_streaming
            scout_task = asyncio.create_task(run_scout_streaming(
                pdf_path=pdf_path,
                model=scout_model,
                on_event=on_event,
                force_vision_inventory=force_vision_inventory,
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
            yield f"event: scout_complete\ndata: {json.dumps({'success': True, 'infopack': infopack_dict})}\n\n"

        except asyncio.CancelledError:
            logger.info("Scout cancelled by user", extra={"session_id": session_id})
            yield f"event: scout_cancelled\ndata: {json.dumps({'message': 'Scout cancelled by user'})}\n\n"

        except Exception as e:
            # The full traceback is logged server-side only. It is NOT sent to
            # the browser: a provider/LLM stack trace can embed request
            # internals or credentials (mirrors the generic-response policy in
            # /api/test-connection). The client gets the exception summary.
            logger.exception("Scout failed", extra={"session_id": session_id})
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        finally:
            # Cancel the scout task if still running (e.g. client disconnected)
            if scout_task is not None and not scout_task.done():
                scout_task.cancel()
                try:
                    await scout_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Clean up the task registry entry to avoid stale references
            task_registry.unregister(session_id, "scout")

    return StreamingResponse(
        scout_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
