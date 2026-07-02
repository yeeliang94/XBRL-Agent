"""Notes-reviewer API routes (docs/PLAN.md — Notes Reviewer, Step 10).

The notes analogue of ``api/reviewer.py`` + ``concept_model/reviewer_routes.py``,
collapsed into one module because the notes reviewer's surface is smaller (it
writes ``notes_cells`` directly — no facts/cross-checks/re-export-from-facts).

Endpoints (all under ``/api/*`` so the auth middleware gates them):
  ``GET  /api/runs/{id}/notes-review``                 — diff + flags + snapshot flag
  ``POST /api/runs/{id}/notes-flags/{flag_id}/answer`` — answer a flag (open→answered)
  ``POST /api/runs/{id}/notes-review/re-review``       — launch a background pass
  ``GET  /api/runs/{id}/notes-review/status``          — poll the latest pass
  ``POST /api/runs/{id}/notes-review/revert-to-original`` — restore original prose
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

import server
from db import repository as repo
from notes.versioning import (
    compute_notes_review_diff,
    has_notes_snapshot,
    revert_notes_to_original,
)

logger = logging.getLogger("server")

router = APIRouter()

_ANSWER_MAX = 8000


class _FlagAnswer(BaseModel):
    answer: str


@router.get("/api/runs/{run_id}/notes-review")
async def get_notes_review(run_id: int):
    """Everything the Notes-review panel renders: the original→reviewer prose
    diff, the reviewer flags, and whether a reviewer version exists."""
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        flags = repo.fetch_notes_review_flags(conn, run_id)
    finally:
        conn.close()
    # The diff helper opens its own connection — call after closing ours.
    diff = compute_notes_review_diff(server.AUDIT_DB_PATH, run_id)
    active_flags = [f for f in flags if f["status"] in ("open", "answered")]
    has_version = has_notes_snapshot(server.AUDIT_DB_PATH, run_id) and (
        bool(diff) or bool(active_flags)
    )
    return {
        "run_id": run_id,
        "has_reviewer_version": has_version,
        "diff": diff,
        "flags": active_flags,
    }


@router.post("/api/runs/{run_id}/notes-flags/{flag_id}/answer")
async def answer_notes_flag(run_id: int, flag_id: int, body: _FlagAnswer):
    """Attach human guidance to a notes flag; move it open → answered."""
    if not (body.answer and body.answer.strip()):
        raise HTTPException(status_code=400, detail="answer must be non-empty.")
    if len(body.answer) > _ANSWER_MAX:
        raise HTTPException(
            status_code=422, detail=f"answer exceeds {_ANSWER_MAX} characters.",
        )
    conn = server._open_audit_conn()
    try:
        ok = repo.answer_notes_review_flag(
            conn, flag_id=flag_id, run_id=run_id, answer=body.answer.strip(),
        )
        conn.commit()
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"ok": True, "id": flag_id, "status": "answered"}


@router.post("/api/runs/{run_id}/notes-review/re-review")
async def re_review_notes(run_id: int, body: Optional[dict] = None):
    """Launch a notes-reviewer pass over the run's CURRENT prose in the
    background. Returns ``{ok, status:"running", model}`` immediately; the Notes
    panel polls :func:`re_review_notes_status`. A pass already running is
    reported back rather than double-launched (re-entrancy guard).
    """
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        # Mirror of the notes-formatter launch guard: both passes write
        # notes_cells prose rows, so neither may start over the other.
        if repo.any_notes_format_task_running(conn, run_id):
            raise HTTPException(
                status_code=409,
                detail="A notes formatter pass is running for this run; "
                       "wait for it to finish before re-reviewing.",
            )
    finally:
        conn.close()

    config = run.config or {}
    filing_level = config.get("filing_level", "company")
    filing_standard = config.get("filing_standard", "mfrs")

    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")

    override = (body or {}).get("model") if isinstance(body, dict) else None
    if isinstance(override, str) and override.strip():
        override = override.strip()
        if len(override) > 128:
            raise HTTPException(
                status_code=422, detail="model override exceeds 128 characters.")
        known = {m["id"] for m in server._load_available_models() if "id" in m}
        if known and override not in known:
            logger.warning("notes re-review model override %r unknown", override)
    else:
        override = None
    model_name = (
        override
        or server._notes_reviewer_model_name()
        or config.get("model")
        or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    )
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not set. Check Settings.")

    # ATOMICALLY claim the run's re-review slot — a single conditional upsert so
    # two concurrent POSTs can't both launch a pass (SQLite serialises writers;
    # the loser's DO UPDATE WHERE status!='running' changes 0 rows). The claim IS
    # the mandatory 'running' persist (the status poll + revert guard read it), so
    # 503 on a DB failure — no thread unless the row is durable.
    launch_conn = server._open_audit_conn()
    try:
        try:
            claimed = repo.claim_notes_review_task(
                launch_conn, run_id, model=model_name)
            launch_conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("notes re-review launch persist failed for run %s",
                           run_id, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Could not record the re-review launch; please try again.",
            ) from e
        if not claimed:
            existing = repo.fetch_notes_review_task(launch_conn, run_id)
            return {"ok": True, "status": "running", "already_running": True,
                    "model": (existing or {}).get("model")}
    finally:
        launch_conn.close()

    # Resolve the source PDF + merged workbook from the run's output dir.
    pdf_path = None
    if run.output_dir:
        cand = Path(run.output_dir) / "uploaded.pdf"
        if cand.exists():
            pdf_path = str(cand)
    merged_path = getattr(run, "merged_workbook_path", None)
    output_dir = run.output_dir or (str(Path(merged_path).parent) if merged_path else "")

    async def _runner_async() -> dict:
        model = server._create_proxy_model(model_name, proxy_url, api_key)
        return await server._run_notes_reviewer_pass(
            run_id=run_id, db_path=str(server.AUDIT_DB_PATH), pdf_path=pdf_path or "",
            filing_level=filing_level, filing_standard=filing_standard,
            model=model, output_dir=output_dir, merged_workbook_path=merged_path,
            event_queue=None, sidecar_paths=[],
        )

    def _thread_main() -> None:
        try:
            outcome = asyncio.run(_runner_async())
            result = {"ok": not outcome.get("error"), "model": model_name, **outcome}
        except Exception as e:  # noqa: BLE001
            logger.exception("background notes re-review failed for run %s", run_id)
            result = {"ok": False, "model": model_name,
                      "error": f"{type(e).__name__}: {e}"}
        # Terminal write is best-effort (startup reconciles a lost row).
        try:
            tc = server._open_audit_conn()
            try:
                repo.upsert_notes_review_task(tc, run_id, "done",
                                              model=model_name, outcome=result,
                                              error=result.get("error"))
                tc.commit()
            finally:
                tc.close()
        except Exception:  # noqa: BLE001
            logger.warning("failed to persist notes re-review outcome for run %s",
                           run_id, exc_info=True)

    threading.Thread(
        target=_thread_main, name=f"notes-re-review-{run_id}", daemon=True,
    ).start()
    return {"ok": True, "status": "running", "model": model_name}


@router.get("/api/runs/{run_id}/notes-review/status")
async def re_review_notes_status(run_id: int):
    """Poll the latest manual notes re-review: idle | running | done."""
    conn = server._open_audit_conn()
    try:
        state = repo.fetch_notes_review_task(conn, run_id)
    finally:
        conn.close()
    if state is None:
        return {"status": "idle"}
    if state.get("status") == "running":
        return {"status": "running", "model": state.get("model")}
    return {"status": "done", **(state.get("outcome") or {})}


@router.post("/api/runs/{run_id}/notes-review/revert-to-original")
async def revert_notes_endpoint(run_id: int):
    """Restore the run's prose from the original snapshot, then refresh the
    durable merged workbook so the download matches."""
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Refuse to revert while a reviewer pass (auto OR manual) is mid-flight —
    # a delete-all-then-restore racing live writes would corrupt the prose.
    guard_conn = server._open_audit_conn()
    try:
        active = repo.fetch_notes_review_task(guard_conn, run_id)
    finally:
        guard_conn.close()
    if active and active.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="A notes reviewer pass is currently running — wait for it to "
                   "finish before reverting.",
        )

    out = await asyncio.to_thread(
        revert_notes_to_original, str(server.AUDIT_DB_PATH), run_id,
    )
    if not out.get("reverted"):
        raise HTTPException(
            status_code=409,
            detail="No notes-reviewer version exists for this run — nothing to revert.",
        )
    # Refresh the durable merged workbook from the restored notes_cells.
    merged_path = getattr(run, "merged_workbook_path", None)
    if merged_path:
        try:
            import shutil
            from notes.persistence import overlay_notes_cells_into_workbook
            nxt = await asyncio.to_thread(
                overlay_notes_cells_into_workbook,
                xlsx_path=merged_path, run_id=run_id, db_path=str(server.AUDIT_DB_PATH),
                filing_level=(run.config or {}).get("filing_level", "company"),
            )
            if str(nxt) != str(merged_path):
                await asyncio.to_thread(shutil.copyfile, nxt, merged_path)
                Path(nxt).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — revert already committed
            logger.warning("failed to refresh merged workbook after notes revert",
                           exc_info=True)
    return {"ok": True, **out}
