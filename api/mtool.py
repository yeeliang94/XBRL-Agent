"""mTool fill-pipeline routes (docs/PLAN.md, Phase 4).

Endpoints:
  ``GET  /api/runs/{run_id}/mtool-fill``        — the semantic fill doc (JSON)
  ``POST /api/runs/{run_id}/mtool-fill/patch``  — upload an empty mTool
        template, patch it server-side from the run's facts, stream back the
        filled workbook + run report headers.

The whole thing is Excel-free (offline zip surgery), so it runs identically
local and on the cloud. Auth middleware guards ``/api/*`` automatically
(gotcha #24). One patcher, no fork: patching goes through
``mtool.offline_fill.fill_workbook`` — the same function the CLI uses.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

import server
from mtool.column_detect import detect_column_map, overall_confidence
from mtool.exporter import apply_column_map, build_fill_doc
from mtool.offline_fill import fill_workbook, validate_input

logger = logging.getLogger("server")

router = APIRouter()

# Runs whose facts are complete enough to fill from. Mirrors the eval
# from-run gate (gotcha #23): draft/running/failed/aborted are refused.
_FILLABLE_STATUSES = {"completed", "completed_with_errors"}

_MAX_TEMPLATE_BYTES = 25 * 1024 * 1024  # 25 MB — an mTool template is ~100s KB


def _load_fillable_run(run_id: int):
    """Fetch a run and assert it can be filled; raise HTTPException otherwise.

    Returns (run, filing_standard, filing_level, denomination)."""
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in _FILLABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(f"Run is '{run.status}'; mTool fill needs a completed run "
                    "(facts must be final)."),
        )
    config = run.config or {}
    return (
        run,
        config.get("filing_standard", "mfrs"),
        config.get("filing_level", "company"),
        config.get("denomination", "thousands"),
    )


def _build_doc(run_id: int):
    run, standard, level, denom = _load_fillable_run(run_id)
    doc = build_fill_doc(
        server.AUDIT_DB_PATH, run_id,
        filing_standard=standard, filing_level=level, denomination=denom,
    )
    return run, doc


@router.get("/api/runs/{run_id}/mtool-fill")
def get_mtool_fill_doc(run_id: int):
    """Return the semantic fill document for a completed run.

    Columns are unresolved (the operator's template layout isn't known here);
    the download is the seam the CLI or the patch endpoint resolves against a
    real template.
    """
    _, doc = _build_doc(run_id)
    return JSONResponse(doc)


@router.post("/api/runs/{run_id}/mtool-fill/patch")
async def patch_mtool_template(
    run_id: int,
    template: UploadFile = File(...),
    column_map: str | None = Form(default=None),
    strict: bool = Form(default=True),
    force_recalc: bool = Form(default=False),
):
    """Patch an uploaded empty mTool template from the run's facts.

    ``column_map`` (optional JSON string) supplies the physical layout of the
    operator's template. When omitted we auto-detect it; if detection is
    low-confidence we refuse (422) and ask for an explicit map rather than
    risk mis-targeting. Streams back the filled ``.xlsx``; the run report is
    returned in the ``X-mTool-Report`` header (and logged).
    """
    _, doc = _build_doc(run_id)
    if not doc["writes"]:
        raise HTTPException(
            status_code=422,
            detail="Run has no fillable facts (nothing to write).")

    raw = await template.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty upload.")
    if len(raw) > _MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="Template too large.")

    # Request-scoped temp dir under the run's output area; cleaned up after
    # the response streams (never persisted).
    work_root = Path(server.OUTPUT_DIR) / "_mtool_tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=work_root))
    src = tmp / "template.xlsx"
    src.write_bytes(raw)

    # Confirm it's a readable xlsx (zip) before anything else.
    try:
        from mtool.offline_fill import get_sheet_paths, load_workbook_entries
        _, data, _ = load_workbook_entries(str(src))
        get_sheet_paths(data)
    except Exception as exc:  # noqa: BLE001
        _cleanup(tmp)
        raise HTTPException(
            status_code=422,
            detail=f"Upload is not a readable .xlsx workbook: {exc}") from exc

    # Resolve the column map: explicit wins; else auto-detect.
    if column_map:
        try:
            cmap = json.loads(column_map)
        except json.JSONDecodeError as exc:
            _cleanup(tmp)
            raise HTTPException(
                status_code=422,
                detail=f"column_map is not valid JSON: {exc}") from exc
    else:
        detected = detect_column_map(str(src), doc)
        if overall_confidence(detected) != "high":
            _cleanup(tmp)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "column layout could not be auto-detected with "
                             "confidence; supply an explicit column_map",
                    "detected": detected,
                })
        cmap = {s: {"label_column": v["label_column"], "columns": v["columns"]}
                for s, v in detected.items()}

    try:
        ready = apply_column_map(doc, cmap)
    except ValueError as exc:
        _cleanup(tmp)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    errors = validate_input(ready)
    if errors:
        _cleanup(tmp)
        raise HTTPException(status_code=422,
                            detail={"input_errors": errors})

    out = tmp / "filled.xlsx"
    report = fill_workbook(str(src), ready, str(out),
                           strict=strict, force_recalc=force_recalc)

    logger.info("mTool patch run %s: status=%s written=%d unresolved=%d",
                run_id, report["status"], len(report["written"]),
                len(report["unresolved"]))

    # Compact report in a header (full detail is large); the frontend reads it
    # to show the review panel. Keep only the summary + row-level lists.
    header_report = json.dumps({
        "status": report["status"],
        "counts": {k: len(report[k]) for k in (
            "written", "fuzzy_matched", "skipped_formula", "type_changed",
            "unresolved", "ambiguous", "mismatches", "errors")},
        "unresolved": report["unresolved"],
        "skipped_formula": report["skipped_formula"],
        "mismatches": report["mismatches"],
    })

    filename = f"mtool_filled_run{run_id}.xlsx"
    return FileResponse(
        str(out),
        media_type=("application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"),
        filename=filename,
        headers={"X-mTool-Report": header_report},
        background=BackgroundTask(_cleanup, tmp),
    )


def _cleanup(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        logger.warning("mTool temp cleanup failed for %s", path, exc_info=True)
