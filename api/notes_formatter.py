"""Notes formatter API routes.

Launches a style-only AI formatter over one prose notes sheet. The formatter
writes only ``notes_cells.html`` after deterministic content-preservation
checks pass.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pydantic_ai.exceptions import UsageLimitExceeded

import server
from api.notes import _notes_template_index
from db import repository as repo

logger = logging.getLogger("server")

router = APIRouter()


class _NotesFormatLaunch(BaseModel):
    sheet: str
    model: Optional[str] = None


@router.post("/api/runs/{run_id}/notes-format")
async def launch_notes_formatter(run_id: int, body: _NotesFormatLaunch):
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        config = run.config or {}
        standard = config.get("filing_standard", "mfrs")
        level = config.get("filing_level", "company")
        template = next(
            (e for e in _notes_template_index(standard, level)
             if e["sheet"] == body.sheet),
            None,
        )
        if template is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown notes sheet {body.sheet!r} for this run.",
            )
        if template["is_numeric"]:
            raise HTTPException(
                status_code=422,
                detail="Numeric notes sheets are not supported by the formatter prototype.",
            )
    finally:
        conn.close()

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not set. Check Settings.")

    override = body.model.strip() if isinstance(body.model, str) else ""
    if override:
        if len(override) > 128:
            raise HTTPException(
                status_code=422, detail="model override exceeds 128 characters.")
        known = {m["id"] for m in server._load_available_models() if "id" in m}
        if known and override not in known:
            logger.warning("notes formatter model override %r unknown", override)
    model_name = (
        override
        or server._notes_reviewer_model_name()
        or (run.config or {}).get("model")
        or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    )

    launch_conn = server._open_audit_conn()
    try:
        try:
            claimed = repo.claim_notes_format_task(
                launch_conn, run_id, body.sheet, model=model_name,
            )
            launch_conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("notes formatter launch persist failed for run %s",
                           run_id, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Could not record the formatter launch; please try again.",
            ) from exc
        if not claimed:
            existing = repo.fetch_notes_format_task(launch_conn, run_id, body.sheet)
            return {
                "ok": True, "status": "running", "already_running": True,
                "sheet": body.sheet, "model": (existing or {}).get("model"),
            }
    finally:
        launch_conn.close()

    pdf_path = ""
    if run.output_dir:
        cand = Path(run.output_dir) / "uploaded.pdf"
        if cand.exists():
            pdf_path = str(cand)

    timeout = getattr(server, "NOTES_FORMATTER_WALLCLOCK_TIMEOUT", 300.0)

    async def _runner_async() -> dict:
        from notes.formatting_agent import run_notes_formatter
        model = server._create_proxy_model(model_name, proxy_url, api_key)
        coro = run_notes_formatter(
            run_id=run_id, db_path=str(server.AUDIT_DB_PATH),
            pdf_path=pdf_path, sheet=body.sheet, model=model,
        )
        # Bound the whole pass the way the reviewer / notes-validator passes are
        # bounded — without this a hung LLM call leaves the task 'running'
        # forever and the UI polls indefinitely until a restart reconciles it.
        if timeout and timeout != float("inf"):
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    def _thread_main() -> None:
        try:
            outcome = asyncio.run(_runner_async())
            result = {"model": model_name, **outcome}
        except asyncio.TimeoutError:
            logger.warning(
                "notes formatter timed out after %.0fs for run %s sheet %s",
                timeout, run_id, body.sheet,
            )
            result = {
                "ok": False, "model": model_name,
                "error": f"Formatter timed out after {int(timeout)}s.",
                "summary": "Formatter timed out; no changes were saved.",
            }
        except UsageLimitExceeded:
            logger.warning(
                "notes formatter hit its turn budget for run %s sheet %s",
                run_id, body.sheet,
            )
            result = {
                "ok": False, "model": model_name,
                "error": "Formatter reached its turn budget without finishing.",
                "summary": "Formatter stopped at its turn budget; no changes were saved.",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("background notes formatter failed for run %s", run_id)
            result = {
                "ok": False, "model": model_name,
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            tc = server._open_audit_conn()
            try:
                repo.upsert_notes_format_task(
                    tc, run_id, body.sheet, "done", model=model_name,
                    summary=result.get("summary"),
                    confidence=result.get("confidence"),
                    changed_rows=int(result.get("changed_rows") or 0),
                    result=result, error=result.get("error"),
                    before_text_hash=result.get("before_text_hash"),
                    after_text_hash=result.get("after_text_hash"),
                )
                tc.commit()
            finally:
                tc.close()
            logger.info(
                "notes formatter completed run=%s sheet=%s ok=%s changed=%s confidence=%s summary=%r",
                run_id, body.sheet, result.get("ok"),
                result.get("changed_rows"), result.get("confidence"),
                result.get("summary"),
            )
        except Exception:  # noqa: BLE001
            logger.warning("failed to persist notes formatter outcome for run %s",
                           run_id, exc_info=True)

    threading.Thread(
        target=_thread_main, name=f"notes-format-{run_id}-{body.sheet}",
        daemon=True,
    ).start()
    logger.info(
        "notes formatter launched run=%s sheet=%s model=%s", run_id,
        body.sheet, model_name,
    )
    return {"ok": True, "status": "running", "sheet": body.sheet, "model": model_name}


@router.get("/api/runs/{run_id}/notes-format/status")
async def notes_formatter_status(run_id: int, sheet: str):
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        state = repo.fetch_notes_format_task(conn, run_id, sheet)
    finally:
        conn.close()
    if state is None:
        return {"status": "idle", "sheet": sheet}
    if state.get("status") == "running":
        return {"status": "running", "sheet": sheet, "model": state.get("model")}
    return {"status": "done", "sheet": sheet, **state}
