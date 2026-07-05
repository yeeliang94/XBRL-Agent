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
from mtool.notes_exporter import build_notes_fill_doc
from mtool.offline_fill import (
    fill_footnotes, fill_workbook, validate_input, validate_notes_input)

logger = logging.getLogger("server")

router = APIRouter()

# Runs whose facts are complete enough to fill from. Mirrors the eval
# from-run gate (gotcha #23): draft/running/failed/aborted are refused.
_FILLABLE_STATUSES = {"completed", "completed_with_errors"}

_MAX_TEMPLATE_BYTES = 25 * 1024 * 1024  # 25 MB — an mTool template is ~100s KB
# Total UNCOMPRESSED size across all zip members. A zip bomb is small on disk
# but expands hugely; an honest mTool template is a few MB decompressed.
_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
_UPLOAD_CHUNK = 1024 * 1024  # 1 MB


async def _read_capped(upload: UploadFile, cap: int) -> bytes:
    """Read an UploadFile in chunks, aborting with 413 once it exceeds ``cap``.

    Never materialises more than ``cap`` (+ one chunk) in memory — the guard
    runs during the read, not after, so a lying/absent Content-Length can't
    slip a huge body through."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(status_code=413, detail="Template too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def _assert_zip_within_budget(path: str) -> None:
    """Reject a workbook whose members decompress past the uncompressed budget.

    Reads only central-directory metadata (``ZipInfo.file_size``) — no
    decompression — so the check itself is cheap and safe against zip bombs."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            total = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Upload is not a readable .xlsx workbook: {exc}") from exc
    if total > _MAX_UNCOMPRESSED_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Template decompresses to too large a size "
                   f"({total} bytes); refusing to parse.")


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


@router.get("/api/runs/{run_id}/mtool-notes-fill")
def get_mtool_notes_fill_doc(run_id: int):
    """Return the prose-notes footnote fill document for a completed run.

    The notes twin of ``/mtool-fill`` — one ``footnotes`` item per note
    (``label`` + ``html``), resolved to the template's ``fn_*`` at patch time.
    """
    _load_fillable_run(run_id)  # gate: 404/409 like the numeric doc
    return JSONResponse(build_notes_fill_doc(server.AUDIT_DB_PATH, run_id))


@router.post("/api/runs/{run_id}/mtool-fill/patch")
async def patch_mtool_template(
    run_id: int,
    template: UploadFile = File(...),
    column_map: str | None = Form(default=None),
    strict: bool = Form(default=True),
    force_recalc: bool = Form(default=False),
    fill_notes: bool = Form(default=True),
    create_missing_notes: bool = Form(default=False),
):
    """Patch an uploaded empty mTool template from the run's facts.

    ``column_map`` (optional JSON string) supplies the physical layout of the
    operator's template. When omitted we auto-detect it; if detection is
    low-confidence we refuse (422) and ask for an explicit map rather than
    risk mis-targeting. Streams back the filled ``.xlsx``; the run report is
    returned in the ``X-mTool-Report`` header (bounded — see
    ``_bounded_report_header`` — and logged in full).

    The uploaded template is written to a request-scoped temp dir under a
    shared ``OUTPUT_DIR/_mtool_tmp`` staging area and is never persisted: the
    whole body is wrapped so any error path cleans it up, and the success path
    cleans it after streaming.
    """
    _, doc = _build_doc(run_id)
    if not doc["writes"]:
        raise HTTPException(
            status_code=422,
            detail="Run has no fillable facts (nothing to write).")

    # Read in bounded chunks and abort the moment we exceed the cap, so an
    # oversized upload never fully materialises in memory (Content-Length can
    # be absent or lie — the chunk loop is the real guard).
    raw = await _read_capped(template, _MAX_TEMPLATE_BYTES)
    if not raw:
        raise HTTPException(status_code=422, detail="Empty upload.")

    # Request-scoped temp dir under a shared staging area (unique mkdtemp
    # subdir per request). EVERYTHING after this point is wrapped so any raise
    # — an HTTPException from a 422 gate OR an unexpected error from
    # fill_workbook / the zip reader — cleans the temp dir before propagating.
    # The success path returns a FileResponse whose BackgroundTask does the
    # cleanup AFTER streaming, so the except never fires on success.
    work_root = Path(server.OUTPUT_DIR) / "_mtool_tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=work_root))
    try:
        src = tmp / "template.xlsx"
        src.write_bytes(raw)

        # Zip-bomb guard: check the central-directory metadata (cheap — no
        # decompression) and reject before load_workbook_entries expands every
        # member into memory. A legitimate mTool template is well under this.
        _assert_zip_within_budget(str(src))

        # Confirm it's a readable xlsx (zip) before anything else, and keep the
        # loaded entries so the auto-detect path below doesn't re-read the zip.
        try:
            from mtool.offline_fill import (
                get_sheet_paths, load_workbook_entries)
            _, data, _ = load_workbook_entries(str(src))
            get_sheet_paths(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail=f"Upload is not a readable .xlsx workbook: {exc}"
            ) from exc

        # Resolve the column map: explicit wins; else auto-detect.
        if column_map:
            try:
                cmap = json.loads(column_map)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"column_map is not valid JSON: {exc}") from exc
            _validate_cmap_shape(cmap)
        else:
            detected = detect_column_map(str(src), doc, data=data)
            if overall_confidence(detected) != "high":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "column layout could not be auto-detected "
                                 "with confidence; supply an explicit "
                                 "column_map",
                        "detected": detected,
                    })
            cmap = {s: {"label_column": v["label_column"],
                        "columns": v["columns"]}
                    for s, v in detected.items()}

        try:
            ready = apply_column_map(doc, cmap)
        except (ValueError, AttributeError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        errors = validate_input(ready)
        if errors:
            raise HTTPException(status_code=422,
                                detail={"input_errors": errors})

        out = tmp / "filled.xlsx"
        report = fill_workbook(str(src), ready, str(out),
                               strict=strict, force_recalc=force_recalc)

        # Chain the prose-notes fill onto the numeric-filled workbook. Notes
        # touch disjoint zip parts (sharedStrings + +FootnoteTexts), so the
        # numeric edits are preserved. Same offline patcher, no fork.
        final = out
        notes_report = None
        if fill_notes:
            notes_doc = build_notes_fill_doc(server.AUDIT_DB_PATH, run_id)
            if notes_doc["footnotes"] and not validate_notes_input(notes_doc):
                notes_out = tmp / "filled_notes.xlsx"
                notes_report = fill_footnotes(
                    str(out), notes_doc, str(notes_out),
                    create_missing=create_missing_notes)
                final = notes_out

        logger.info(
            "mTool patch run %s: numeric status=%s written=%d unresolved=%d; "
            "notes status=%s written=%d created=%d unresolved=%d",
            run_id, report["status"], len(report["written"]),
            len(report["unresolved"]),
            (notes_report or {}).get("status", "skipped"),
            len((notes_report or {}).get("footnotes_written", [])),
            len((notes_report or {}).get("footnotes_created", [])),
            len((notes_report or {}).get("unresolved", [])))

        filename = f"mtool_filled_run{run_id}.xlsx"
        return FileResponse(
            str(final),
            media_type=("application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"),
            filename=filename,
            headers={"X-mTool-Report": _bounded_report_header(report,
                                                              notes_report)},
            background=BackgroundTask(_cleanup, tmp),
        )
    except Exception:
        _cleanup(tmp)
        raise


def _validate_cmap_shape(cmap) -> None:
    """Reject a structurally-wrong column_map with a 422 (not a 500).

    Expected: ``{sheet: {"label_column": str, "columns": {role: col}}}``.
    apply_column_map assumes this shape; a string/list where a dict belongs
    would otherwise raise AttributeError deep inside → uncaught 500."""
    if not isinstance(cmap, dict):
        raise HTTPException(status_code=422,
                            detail="column_map must be a JSON object")
    for sheet, cfg in cmap.items():
        if not isinstance(cfg, dict):
            raise HTTPException(
                status_code=422,
                detail=f"column_map[{sheet!r}] must be an object with "
                       "'label_column' and 'columns'")
        cols = cfg.get("columns")
        if cols is not None and not isinstance(cols, dict):
            raise HTTPException(
                status_code=422,
                detail=f"column_map[{sheet!r}].columns must be an object")


# Row-detail lists are truncated in the header so a run with many unresolved
# labels can't blow past proxy header limits (~8 KB) and get the whole response
# rejected/truncated. Counts are always exact; a `truncated` flag tells the UI
# detail was elided (full detail is in the server log).
_HEADER_LIST_CAP = 20
_HEADER_MAX_BYTES = 6000


def _notes_report_block(notes_report: dict | None) -> dict | None:
    """Compact notes-fill summary for the response header. ``None`` when notes
    weren't filled (fill_notes off, or the run has none)."""
    if not notes_report:
        return None
    return {
        "status": notes_report["status"],
        "counts": {
            "written": len(notes_report.get("footnotes_written", [])),
            "created": len(notes_report.get("footnotes_created", [])),
            "unresolved": len(notes_report.get("unresolved", [])),
            "mismatches": len(notes_report.get("footnote_mismatches", [])),
            "errors": len(notes_report.get("errors", [])),
        },
        "unresolved": [
            {"label": e.get("label"), "detail": e.get("detail")}
            for e in notes_report.get("unresolved", [])[:_HEADER_LIST_CAP]
        ],
    }


def _bounded_report_header(report: dict, notes_report: dict | None = None) -> str:
    detail_keys = ("unresolved", "skipped_formula", "mismatches")
    counts = {k: len(report[k]) for k in (
        "written", "fuzzy_matched", "skipped_formula", "type_changed",
        "unresolved", "ambiguous", "mismatches", "errors")}
    truncated = any(len(report[k]) > _HEADER_LIST_CAP for k in detail_keys)
    notes_block = _notes_report_block(notes_report)
    payload = {
        "status": report["status"],
        "counts": counts,
        "truncated": truncated,
        **({"notes": notes_block} if notes_block else {}),
        **{k: report[k][:_HEADER_LIST_CAP] for k in detail_keys},
    }
    encoded = json.dumps(payload)
    if len(encoded.encode("utf-8")) <= _HEADER_MAX_BYTES:
        return encoded
    # Still too big (very long labels): drop the detail lists, keep counts.
    slim = {"status": report["status"], "counts": counts, "truncated": True}
    if notes_block:
        slim["notes"] = {"status": notes_block["status"],
                         "counts": notes_block["counts"]}
    return json.dumps(slim)


def _cleanup(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        logger.warning("mTool temp cleanup failed for %s", path, exc_info=True)
