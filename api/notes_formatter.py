"""Notes formatter API routes.

Launches a style-only AI formatter over one prose notes sheet. The formatter
writes only ``notes_cells.html`` after deterministic content-preservation
checks pass.
"""
from __future__ import annotations

import asyncio
import json
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
        # Lifecycle interlocks: formatting is post-extraction review tooling.
        # The write-time compare-and-swap makes concurrent writers *safe*;
        # these guards make them *not confusing* (no pass that silently skips
        # most of its rows because another writer owned the sheet).
        if run.status not in repo._TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Run is {run.status}; formatting is available once "
                       "the run has finished.",
            )
        # (The reviewer-not-running interlock lives INSIDE the atomic claim
        # below — checking it here would be a TOCTOU against a concurrent
        # reviewer launch.)
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
                detail="Numeric notes sheets are not supported by the formatter.",
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
        or server._notes_formatter_model_name()
        or config.get("model")
        or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    )

    launch_conn = server._open_audit_conn()
    try:
        try:
            # Atomic "reviewer not running + claim slot" — one BEGIN IMMEDIATE
            # transaction inside the helper, so a concurrent reviewer launch
            # can't interleave between the check and the claim.
            outcome = repo.claim_notes_format_task_guarded(
                launch_conn, run_id, body.sheet, model=model_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("notes formatter launch persist failed for run %s",
                           run_id, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Could not record the formatter launch; please try again.",
            ) from exc
        if outcome == "reviewer_running":
            raise HTTPException(
                status_code=409,
                detail="A notes reviewer pass is running for this run; "
                       "wait for it to finish before formatting.",
            )
        if outcome == "format_running":
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

    timeout = server.NOTES_FORMATTER_WALLCLOCK_TIMEOUT

    async def _runner_async() -> dict:
        from notes.formatting_agent import run_notes_formatter
        model = server._create_proxy_model(model_name, proxy_url, api_key)
        coro = run_notes_formatter(
            run_id=run_id, db_path=str(server.AUDIT_DB_PATH),
            pdf_path=pdf_path, sheet=body.sheet, model=model,
            output_dir=run.output_dir or "",
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
                "ok": False, "model": model_name, "error_type": "timeout",
                "error": f"Formatter timed out after {int(timeout)}s.",
                "summary": "Formatter timed out; no changes were saved.",
            }
        except UsageLimitExceeded:
            logger.warning(
                "notes formatter hit its turn budget for run %s sheet %s",
                run_id, body.sheet,
            )
            result = {
                "ok": False, "model": model_name, "error_type": "turn_budget",
                "error": "Formatter reached its turn budget without finishing.",
                "summary": "Formatter stopped at its turn budget; no changes were saved.",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("background notes formatter failed for run %s", run_id)
            result = {
                "ok": False, "model": model_name, "error_type": "model_error",
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
                    error_type=result.get("error_type"),
                    before_text_hash=result.get("before_text_hash"),
                    after_text_hash=result.get("after_text_hash"),
                    prompt_tokens=int(result.get("prompt_tokens") or 0),
                    completion_tokens=int(result.get("completion_tokens") or 0),
                    cache_read_tokens=int(result.get("cache_read_tokens") or 0),
                    cache_write_tokens=int(result.get("cache_write_tokens") or 0),
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
        has_snapshot = bool(
            repo.fetch_notes_format_snapshots(conn, run_id, sheet),
        )
    finally:
        conn.close()
    if state is None:
        return {"status": "idle", "sheet": sheet, "can_revert": False}
    if state.get("status") == "running":
        return {"status": "running", "sheet": sheet, "model": state.get("model")}
    # Lift skipped_rows out of result_json so the panel can render the
    # "edited during formatting" note without unpacking the whole result.
    skipped = (state.get("result") or {}).get("skipped_rows") or []
    return {
        "status": "done", "sheet": sheet, "skipped_rows": skipped,
        "can_revert": has_snapshot, **state,
    }


@router.get("/api/runs/{run_id}/notes-format/trace")
async def notes_formatter_trace(run_id: int, sheet: str):
    """Serve the formatter pass's conversation trace (gotcha #6 pattern).

    Security: `sheet` is validated against the run's template index before
    touching the filesystem, and the resolved path must stay under the run's
    output_dir — a caller can't traverse via the query param.
    """
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    config = run.config or {}
    known_sheets = {
        e["sheet"]
        for e in _notes_template_index(
            config.get("filing_standard", "mfrs"),
            config.get("filing_level", "company"),
        )
    }
    if sheet not in known_sheets:
        raise HTTPException(
            status_code=400, detail=f"Unknown notes sheet {sheet!r} for this run.",
        )
    if not run.output_dir:
        raise HTTPException(status_code=404, detail="Run has no output directory")
    out_root = Path(run.output_dir).resolve()
    trace_path = (
        out_root / f"notes_format_{sheet}_conversation_trace.json"
    ).resolve()
    if not trace_path.is_relative_to(out_root):
        raise HTTPException(status_code=400, detail="Invalid trace path")
    if not trace_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No formatter trace captured for {sheet}.",
        )
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read trace: {exc}")


class _NotesFormatRevert(BaseModel):
    sheet: str


@router.post("/api/runs/{run_id}/notes-format/revert")
async def revert_notes_formatter(run_id: int, body: _NotesFormatRevert):
    """Restore the sheet's pre-format HTML from the v27 snapshot.

    Revert is pure-style: the deterministic verifier guaranteed the formatted
    HTML renders the same text as the snapshot, so restoring it can never
    lose content. Rows deleted since the pass (regenerate) are left alone.
    """
    from notes.format_verify import verify_format_only
    from notes.html_sanitize import sanitize_notes_html

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        # One write lock across check → verify → restore, so a concurrent
        # PATCH or pass launch can't interleave (an early HTTPException
        # leaves the transaction to roll back on close).
        conn.execute("BEGIN IMMEDIATE")
        task = repo.fetch_notes_format_task(conn, run_id, body.sheet)
        if task and task.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail="A formatter pass is running for this sheet; wait for "
                       "it to finish before reverting.",
            )
        # Same interlock as launch: the notes reviewer writes these prose
        # rows too, so a revert must not race a running reviewer pass.
        review_task = repo.fetch_notes_review_task(conn, run_id)
        if review_task and review_task.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail="A notes reviewer pass is running for this run; "
                       "wait for it to finish before reverting.",
            )
        snapshot = repo.fetch_notes_format_snapshots(conn, run_id, body.sheet)
        if not snapshot:
            raise HTTPException(
                status_code=404,
                detail="No formatting snapshot to revert for this sheet.",
            )
        cells = {
            c.row: c
            for c in repo.list_notes_cells_for_run(conn, run_id)
            if c.sheet == body.sheet
        }
        restored_rows: list[int] = []
        skipped_rows: list[int] = []
        for row, html in snapshot.items():
            cell = cells.get(row)
            if cell is None:
                skipped_rows.append(row)  # deleted since the pass
                continue
            # The formatter's write was style-only, so snapshot vs current
            # must still be CONTENT-equal. If the user edited content after
            # formatting, restoring the snapshot would clobber that edit —
            # skip the row instead (the verifier gate cuts the other way
            # here: it protects the newer content, not the older).
            vr = verify_format_only(html, cell.html or "")
            if not vr.ok:
                skipped_rows.append(row)
                continue
            # Snapshots originate from already-sanitised DB rows, but every
            # notes_cells write goes through the sanitiser (gotcha #16 flow)
            # — defence-in-depth against a tampered snapshot row.
            cleaned, _warnings = sanitize_notes_html(html)
            if repo.cas_update_notes_cell_html(
                conn, run_id=run_id, sheet=body.sheet, row=row,
                expected_html=cell.html, new_html=cleaned,
            ):
                restored_rows.append(row)
            else:
                skipped_rows.append(row)
        summary = "Formatting reverted to the pre-format state."
        if skipped_rows:
            summary += (
                f" {len(skipped_rows)} row(s) kept — content edited after "
                "formatting."
            )
        repo.upsert_notes_format_task(
            conn, run_id, body.sheet, "done",
            model=(task or {}).get("model"),
            summary=summary,
            changed_rows=0, error=None, error_type="reverted",
            result={
                "ok": True, "reverted": True,
                "restored_rows": restored_rows, "skipped_rows": skipped_rows,
            },
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(
        "notes formatter reverted run=%s sheet=%s restored=%d skipped=%d",
        run_id, body.sheet, len(restored_rows), len(skipped_rows),
    )
    return {
        "ok": True, "status": "done", "sheet": body.sheet,
        "restored_rows": len(restored_rows), "skipped_rows": skipped_rows,
        "error_type": "reverted",
    }
