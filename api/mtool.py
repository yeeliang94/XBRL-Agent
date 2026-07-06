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
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

import server
from mtool.column_detect import detect_column_map, overall_confidence
from mtool.exporter import apply_column_map, build_fill_doc
from mtool.notes_decorate import NotesTableStyle
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
# A directory bomb is small on disk with a huge member count; an honest mTool
# template has a few dozen parts. Bound it so load_workbook_entries never
# builds a pathological dict.
_MAX_ZIP_MEMBERS = 5000
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
            infos = zf.infolist()
            total = sum(info.file_size for info in infos)
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Upload is not a readable .xlsx workbook: {exc}") from exc
    if len(infos) > _MAX_ZIP_MEMBERS:
        raise HTTPException(
            status_code=413,
            detail=f"Template has too many zip members ({len(infos)}); "
                   "refusing to parse.")
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


def _resolve_notes_style(run) -> NotesTableStyle:
    """The effective notes-table style for a run's mTool fill: the per-run
    override (``runs.notes_table_style``, a full snapshot — gotcha #16) wins,
    else the firm-wide default (``XBRL_NOTES_TABLE_STYLE``), else the historic
    baseline. Mirrors the frontend ``resolveTheme`` precedence so the mTool
    paste matches the in-app editor preview and the manual Copy → paste."""
    theme = getattr(run, "notes_table_style", None) or server._notes_table_style()
    return NotesTableStyle.from_theme(theme)


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
    run, *_ = _load_fillable_run(run_id)  # gate: 404/409 like the numeric doc
    return JSONResponse(build_notes_fill_doc(
        server.AUDIT_DB_PATH, run_id, style=_resolve_notes_style(run)))


@router.post("/api/runs/{run_id}/mtool-fill/patch")
async def patch_mtool_template(
    run_id: int,
    template: UploadFile = File(...),
    column_map: str | None = Form(default=None),
    strict: bool = Form(default=True),
    force_recalc: bool = Form(default=False),
    fill_notes: bool = Form(default=True),
    create_missing_notes: bool = Form(default=False),
    notes_targets: str | None = Form(default=None),
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
    run, doc = _build_doc(run_id)
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
        # Notes fill targets EXISTING popup slots by default; with
        # ``create_missing_notes`` on, a note whose concept has no fn_* yet is
        # created at the trigger cell discovered from its visible label
        # (col-D label -> col-E trigger; fill_footnotes.create_missing). That
        # path is fragile (mTool only renders a native-shaped slot), so it's
        # opt-in. Notes-doc-level strict is honored.
        # Notes fill is best-effort (mirrors the CLI + notes-validator
        # fail-soft, gotcha #22). A validation failure or an unexpected raise
        # must NOT discard the already-good numeric fill: keep `final = out`
        # (numeric-only) and report the notes side as `degraded` so the modal's
        # combined status surfaces it instead of silently reporting "skipped".
        final = out
        notes_report = None
        if fill_notes:
            try:
                notes_doc = build_notes_fill_doc(
                    server.AUDIT_DB_PATH, run_id,
                    style=_resolve_notes_style(run))
                # Operator-chosen placements for ambiguous/near-miss notes
                # (the preview's decision UI). 422s on a malformed payload —
                # a bad explicit target must fail loudly, not fall back to
                # the label guess the operator just overrode.
                _apply_notes_targets(notes_doc, notes_targets)
                if notes_doc["footnotes"]:
                    notes_errors = validate_notes_input(notes_doc)
                    if notes_errors:
                        # Machine-generated doc — this should never happen; if
                        # it does, don't collapse it into "skipped".
                        logger.warning(
                            "mTool patch run %s: notes fill doc failed "
                            "validation, skipping notes: %s",
                            run_id, notes_errors)
                        notes_report = {
                            "status": "degraded",
                            "errors": [{"detail": e} for e in notes_errors]}
                    else:
                        notes_out = tmp / "filled_notes.xlsx"
                        notes_report = fill_footnotes(
                            str(out), notes_doc, str(notes_out),
                            create_missing=create_missing_notes)
                        final = notes_out
            except HTTPException:
                # A malformed notes_targets is a caller error (422), not a
                # best-effort degrade — the operator just overrode the label
                # guess, so silently falling back to it would be worse.
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mTool patch run %s: notes fill raised, returning "
                    "numeric-only workbook", run_id, exc_info=True)
                notes_report = {"status": "degraded",
                                "errors": [{"detail": str(exc)}]}
                final = out

        logger.info(
            "mTool patch run %s: numeric status=%s written=%d unresolved=%d; "
            "notes status=%s written=%d created=%d unresolved=%d "
            "(create_missing_notes=%s)",
            run_id, report["status"], len(report["written"]),
            len(report["unresolved"]),
            (notes_report or {}).get("status", "skipped"),
            len((notes_report or {}).get("footnotes_written", [])),
            len((notes_report or {}).get("footnotes_created", [])),
            len((notes_report or {}).get("unresolved", [])),
            create_missing_notes)
        # The per-note unresolved reasons are the actionable diagnostic (why a
        # note didn't fill: no slot / strict mismatch). The header is bounded,
        # so log them in full here for debugging.
        for u in (notes_report or {}).get("unresolved", []):
            logger.info("mTool notes unresolved run %s: label=%r detail=%r",
                        run_id, u.get("label"), u.get("detail"))

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


@router.post("/api/runs/{run_id}/mtool-fill/detect-columns")
async def detect_mtool_columns(
    run_id: int,
    template: UploadFile = File(...),
):
    """Detect the uploaded template's column layout WITHOUT writing anything.

    The pre-flight the modal runs the moment a template is chosen, so the
    operator confirms the label/figure columns UP FRONT (alongside the notes
    check) instead of discovering a low-confidence layout only after a failed
    Fill (the old submit → 422 → confirm → retry loop). Returns the detected
    map plus an overall ``confidence`` (``high`` = safe to fill as-is; anything
    else = please confirm). The patch endpoint still auto-detects + 422s as a
    defensive fallback for callers that skip this step.
    """
    run, doc = _build_doc(run_id)

    raw = await _read_capped(template, _MAX_TEMPLATE_BYTES)
    if not raw:
        raise HTTPException(status_code=422, detail="Empty upload.")

    work_root = Path(server.OUTPUT_DIR) / "_mtool_tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=work_root))
    try:
        src = tmp / "template.xlsx"
        src.write_bytes(raw)
        _assert_zip_within_budget(str(src))

        from mtool.offline_fill import get_sheet_paths, load_workbook_entries
        try:
            _, data, _ = load_workbook_entries(str(src))
            get_sheet_paths(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail=f"Upload is not a readable .xlsx workbook: {exc}"
            ) from exc

        detected = detect_column_map(str(src), doc, data=data)
        return JSONResponse({
            "detected": detected,
            "confidence": overall_confidence(detected),
        })
    finally:
        _cleanup(tmp)


@router.post("/api/runs/{run_id}/mtool-fill/notes-preview")
async def preview_mtool_notes(
    run_id: int,
    template: UploadFile = File(...),
    create_missing_notes: bool = Form(default=False),
    notes_targets: str | None = Form(default=None),
):
    """Dry-run the prose-notes fill against an uploaded template and return the
    plan WITHOUT writing anything — the notes diagnostic the operator runs
    before committing. For every note in the run it reports one of: fills an
    existing ``fn_*`` slot, gets a new slot CREATED (only when
    ``create_missing_notes`` is on), or stays unresolved (with the reason).

    ``template_fn_slots`` (how many ``fn_*`` the uploaded template already
    exposes) makes the two failure modes distinguishable: *few/zero slots* ->
    the concepts aren't popup-backed yet (turn on create-missing or add the
    text blocks in mTool); *many slots but notes still unresolved* -> the
    labels differ from mTool's (a matching problem, not a missing-slot one).
    """
    run, *_ = _load_fillable_run(run_id)  # 404/409 gate, same as the patch endpoint
    notes_doc = build_notes_fill_doc(
        server.AUDIT_DB_PATH, run_id, style=_resolve_notes_style(run))
    # Re-preview honours the operator's placements so the plan updates as
    # decisions are made (same seam the patch endpoint applies).
    _apply_notes_targets(notes_doc, notes_targets)

    raw = await _read_capped(template, _MAX_TEMPLATE_BYTES)
    if not raw:
        raise HTTPException(status_code=422, detail="Empty upload.")

    work_root = Path(server.OUTPUT_DIR) / "_mtool_tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=work_root))
    try:
        src = tmp / "template.xlsx"
        src.write_bytes(raw)
        _assert_zip_within_budget(str(src))

        from mtool.offline_fill import (
            get_sheet_paths, inspect_footnotes, load_workbook_entries)
        try:
            _, data, _ = load_workbook_entries(str(src))
            get_sheet_paths(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail=f"Upload is not a readable .xlsx workbook: {exc}"
            ) from exc

        existing_slots = len(inspect_footnotes(data)["targets"])
        base = {
            "notes_in_run": len(notes_doc["footnotes"]),
            "template_fn_slots": existing_slots,
            "create_missing_notes": create_missing_notes,
            "counts": notes_doc["meta"]["counts"],
        }
        if not notes_doc["footnotes"]:
            return JSONResponse({**base, "will_fill_existing": [],
                                 "will_create": [], "unresolved": [],
                                 "errors": []})

        # dry_run: resolves + plans, writes nothing (output_path unused).
        report = fill_footnotes(str(src), notes_doc, None, dry_run=True,
                                create_missing=create_missing_notes)
        created_keys = {c.get("key") for c in report["footnotes_created"]}
        return JSONResponse({
            **base,
            "will_create": [
                {"index": c.get("index"), "label": c.get("label"),
                 "cell": c.get("visible_cell")
                 or (f"{c.get('sheet')}!{c.get('cell')}"
                     if c.get("sheet") and c.get("cell") else None),
                 "label_cell": c.get("label_cell")}
                for c in report["footnotes_created"]],
            "will_fill_existing": [
                {"index": w.get("index"), "label": w.get("label"),
                 "key": w.get("key")}
                for w in report["footnotes_written"]
                if w.get("key") not in created_keys],
            # Full structured entries (reason code, candidates, near-miss
            # suggestion) — the modal's decision UI is built on these.
            "unresolved": [_unresolved_entry(u)
                           for u in report["unresolved"]],
            "errors": report.get("errors", []),
        })
    finally:
        _cleanup(tmp)


_CELL_REF_RE = re.compile(r"[A-Z]{1,3}\d+$")
_FN_KEY_RE = re.compile(r"fn_\d+$")


def _apply_notes_targets(notes_doc: dict, notes_targets: str | None) -> None:
    """Apply operator-chosen placements to the machine-built notes doc.

    ``notes_targets`` is a JSON object keyed by the note's INDEX in the doc's
    ``footnotes`` list (the stable id the preview's unresolved entries carry):
    ``{"3": {"key": "fn_12"}}`` pins a note to an existing slot;
    ``{"5": {"sheet": "Notes-CI", "cell": "E14"}}`` pins it to an explicit
    visible trigger cell (the create path). This is the notes twin of the
    numeric ``column_map`` confirm-and-retry seam: the tool never guesses on
    an ambiguous/near-miss label, so the human resolves it here instead of
    dead-ending. An explicit cell replaces label matching for that item
    (the label is dropped so the fill takes the explicit-cell path)."""
    if not notes_targets:
        return
    try:
        targets = json.loads(notes_targets)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"notes_targets is not valid JSON: {exc}") from exc
    if not isinstance(targets, dict):
        raise HTTPException(
            status_code=422,
            detail="notes_targets must be a JSON object keyed by note index")
    items = notes_doc.get("footnotes") or []
    for raw_idx, tgt in targets.items():
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"notes_targets key {raw_idx!r} is not a note index")
        if not 0 <= idx < len(items):
            raise HTTPException(
                status_code=422,
                detail=f"notes_targets index {idx} is out of range "
                       f"(run has {len(items)} notes)")
        if not isinstance(tgt, dict):
            raise HTTPException(
                status_code=422,
                detail=f"notes_targets[{raw_idx}] must be an object")
        item = items[idx]
        key = tgt.get("key")
        sheet, cell = tgt.get("sheet"), tgt.get("cell")
        if key:
            if not (isinstance(key, str) and _FN_KEY_RE.fullmatch(key)):
                raise HTTPException(
                    status_code=422,
                    detail=f"notes_targets[{raw_idx}].key must look like "
                           "'fn_12'")
            item["key"] = key
        elif sheet and cell:
            cell = str(cell).upper()
            if not _CELL_REF_RE.fullmatch(cell):
                raise HTTPException(
                    status_code=422,
                    detail=f"notes_targets[{raw_idx}].cell must be a cell "
                           "reference like 'E14'")
            item["sheet"], item["cell"] = str(sheet), cell
            item.pop("label", None)
        else:
            raise HTTPException(
                status_code=422,
                detail=f"notes_targets[{raw_idx}] needs 'key' or "
                       "'sheet'+'cell'")


# Structured fields passed through from a fill/preview unresolved entry to the
# client — the guidance payload the modal's decision UI is built on (reason
# code + pick-one candidates + the refused near-miss suggestion).
_UNRESOLVED_PASSTHROUGH = (
    "index", "label", "detail", "reason", "candidates",
    "matched_label", "ratio", "key", "sheet", "cell", "label_cell")


def _unresolved_entry(u: dict) -> dict:
    return {k: u.get(k) for k in _UNRESOLVED_PASSTHROUGH
            if u.get(k) is not None}


_COLUMN_LETTER_RE = re.compile(r"[A-Z]{1,3}$")


def _validate_cmap_shape(cmap) -> None:
    """Reject a structurally-wrong column_map with a 422 (not a 500).

    Expected: ``{sheet: {"label_column": str, "columns": {role: col}}}`` where
    every column is an uppercase letter (``"D"``, ``"AA"``). apply_column_map
    assumes this shape; a string/list where a dict belongs would otherwise
    raise AttributeError deep inside → uncaught 500, and a non-letter column
    (``"1"``/``"foo"``) would surface only as a buried per-write error rather
    than a clean up-front rejection."""
    if not isinstance(cmap, dict):
        raise HTTPException(status_code=422,
                            detail="column_map must be a JSON object")
    for sheet, cfg in cmap.items():
        if not isinstance(cfg, dict):
            raise HTTPException(
                status_code=422,
                detail=f"column_map[{sheet!r}] must be an object with "
                       "'label_column' and 'columns'")
        label_col = cfg.get("label_column")
        if label_col is not None and not (
                isinstance(label_col, str)
                and _COLUMN_LETTER_RE.fullmatch(label_col)):
            raise HTTPException(
                status_code=422,
                detail=f"column_map[{sheet!r}].label_column must be a column "
                       "letter like 'D'")
        cols = cfg.get("columns")
        if cols is not None and not isinstance(cols, dict):
            raise HTTPException(
                status_code=422,
                detail=f"column_map[{sheet!r}].columns must be an object")
        for role, col in (cols or {}).items():
            if not (isinstance(col, str)
                    and _COLUMN_LETTER_RE.fullmatch(col)):
                raise HTTPException(
                    status_code=422,
                    detail=f"column_map[{sheet!r}].columns[{role!r}] must be "
                           "a column letter like 'E'")


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
    # Top-level status is COMBINED: a degraded notes fill must not hide behind a
    # green numeric status (the modal keys its "Clean / safe to Validate" banner
    # off this). numeric_status keeps the two distinguishable for detail.
    overall = report["status"]
    if notes_block and notes_block["status"] != "ok":
        overall = "degraded"
    payload = {
        "status": overall,
        "numeric_status": report["status"],
        "counts": counts,
        "truncated": truncated,
        **({"notes": notes_block} if notes_block else {}),
        **{k: report[k][:_HEADER_LIST_CAP] for k in detail_keys},
    }
    encoded = json.dumps(payload)
    if len(encoded.encode("utf-8")) <= _HEADER_MAX_BYTES:
        return encoded
    # Still too big (very long labels): drop the detail lists, keep counts.
    slim = {"status": overall, "numeric_status": report["status"],
            "counts": counts, "truncated": True}
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
