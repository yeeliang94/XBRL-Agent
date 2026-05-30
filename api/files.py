"""File-serving routes: source-PDF viewer + workbook/result downloads.

Endpoints:
  ``GET /api/runs/{run_id}/pdf/info``              — source PDF page count
  ``GET /api/runs/{run_id}/pdf/page/{page}.png``   — render one page to PNG
  ``GET /api/runs/{run_id}/download/filled``       — stream the merged workbook
  ``GET /api/result/{session_id}/{filename}``      — whitelisted per-run downloads

The route-local helpers/constants (``_resolve_run_pdf_path``, the DPI clamp,
``_remove_overlay_tempfiles``, the download whitelist) live here since nothing
else uses them. Shared run/fact helpers are read through ``server.X``.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

import server

logger = logging.getLogger("server")

router = APIRouter()


def _resolve_run_pdf_path(run: "Any") -> Optional[Path]:
    """Locate the source PDF that was uploaded for a run, or None.

    The upload writes the file to ``OUTPUT_DIR/{session_id}/uploaded.pdf``
    (server.py upload handler), and ``session_id`` is the canonical output
    directory name. We prefer it; for older rows that predate the session_id
    column we fall back to the merged-workbook's parent dir, which is the same
    session folder. Returns None when neither yields an existing file — the
    PDF pane then shows an empty state instead of erroring (legacy / CLI runs
    never copied a PDF into an output dir).
    """
    candidates: list[Path] = []
    if run.session_id:
        candidates.append(server.OUTPUT_DIR / run.session_id / "uploaded.pdf")
    if run.merged_workbook_path:
        candidates.append(Path(run.merged_workbook_path).parent / "uploaded.pdf")
    # Defense-in-depth: session_id is a server-minted UUID in practice, so
    # these inputs are DB- not URL-controlled — but mirror the hardening on
    # /api/result/ anyway. Resolve each candidate and require it to live under
    # OUTPUT_DIR; a `..`-laden session_id can't escape the output tree.
    output_root = server.OUTPUT_DIR.resolve()
    for path in candidates:
        try:
            resolved = path.resolve()
            resolved.relative_to(output_root)
        except (ValueError, OSError):
            continue
        if resolved.exists():
            return resolved
    return None


@router.get("/api/runs/{run_id}/pdf/info")
async def pdf_info_endpoint(run_id: int):
    """Return the source PDF's page count so the viewer can bound paging.

    404 when the run is unknown or has no stored PDF — the frontend treats
    that as "no source available" and degrades gracefully.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    pdf_path = _resolve_run_pdf_path(run)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="No source PDF stored for this run.")
    from tools.pdf_viewer import count_pdf_pages
    try:
        # count_pdf_pages opens the document with PyMuPDF; a corrupt/locked
        # file raises, which we surface as a 422 rather than a 500.
        pages = await asyncio.to_thread(count_pdf_pages, str(pdf_path))
    except Exception as exc:  # noqa: BLE001 — bad file is a client-visible condition
        raise HTTPException(status_code=422, detail=f"Could not read source PDF: {exc}")
    return {"run_id": run_id, "pages": pages}


# Clamp the render resolution: too low is illegible, too high blows up the
# payload and render time. 150 DPI is a readable default for statement pages.
_PDF_MIN_DPI = 72
_PDF_MAX_DPI = 250
_PDF_DEFAULT_DPI = 150


@router.get("/api/runs/{run_id}/pdf/page/{page}.png")
async def pdf_page_endpoint(run_id: int, page: int, dpi: int = _PDF_DEFAULT_DPI):
    """Render one source-PDF page (1-indexed) to a PNG for side-by-side review.

    Reuses ``tools.pdf_viewer.render_pages_to_png_bytes`` (and its module-level
    page cache) — the same renderer the scout/correction/extraction agents use.
    This is a read-only, image-only path: it does NOT widen the file-download
    whitelist, so the raw PDF itself is never served.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    pdf_path = _resolve_run_pdf_path(run)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="No source PDF stored for this run.")
    from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes

    dpi = max(_PDF_MIN_DPI, min(_PDF_MAX_DPI, dpi))

    try:
        total_pages = await asyncio.to_thread(count_pdf_pages, str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read source PDF: {exc}")
    if page < 1 or page > total_pages:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} out of range (document has {total_pages} pages).",
        )

    # Heavy raster work off the event loop so concurrent page requests don't
    # serialise behind one another. A render failure (corrupt page, PyMuPDF
    # error) is a client-visible bad-input condition → 422, not a 500.
    try:
        png_bytes = (
            await asyncio.to_thread(
                render_pages_to_png_bytes, str(pdf_path), page, page, dpi
            )
        )[0]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not render page {page}: {exc}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # Pages are immutable for a finished run, so let the browser cache
            # them; the viewer re-renders nothing on a second visit.
            "Cache-Control": "private, max-age=3600",
            "X-PDF-Page-Count": str(total_pages),
        },
    )


@router.get("/api/runs/{run_id}/download/filled")
async def download_filled_endpoint(run_id: int):
    """Stream the merged workbook for a past run.

    Single source of truth for the file path is `runs.merged_workbook_path`.
    We explicitly do NOT derive the path from session_id or probe the
    filesystem — if the stored path no longer exists on disk we return a
    clear 404 instead of a 500.

    Step 7 of the notes rich-editor plan: when `notes_cells` has rows
    for this run, the canonical notes content lives in the DB (edited
    via the post-run editor). We overlay those cells onto a temp copy
    of the on-disk workbook at stream time so the download always
    reflects the latest HTML → flattened-plaintext rendering.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.merged_workbook_path:
        raise HTTPException(
            status_code=404,
            detail="This run has no merged workbook (likely failed before merge).",
        )
    wb_path = Path(run.merged_workbook_path)
    if not wb_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Merged workbook file no longer exists on disk: {wb_path}",
        )

    # Phase 1.3: when the run has canonical facts, the DB is authoritative
    # (it carries cascaded totals + any review-UI edits). Rebuild the merged
    # workbook from those facts into a temp file so the download reflects the
    # latest edits without a manual "regenerate" step. Falls back to the
    # on-disk workbook when re-export isn't applicable or fails. Temp files
    # are tracked for cleanup after streaming; the on-disk file is never
    # deleted.
    temp_paths: list[Path] = []
    base_path = wb_path
    if server._run_has_facts(server.AUDIT_DB_PATH, run_id):
        reexported = await asyncio.to_thread(
            server._reexport_and_remerge_from_facts, run_id
        )
        if reexported is not None:
            base_path = reexported
            temp_paths.append(reexported)
        elif run.ended_at:
            # Re-export failed. The on-disk workbook reflects facts AS OF the
            # pipeline run but NOT post-run manual edits — serving it silently
            # would hand the user a file missing their edits. Fail closed when
            # such edits exist (peer-review): a clear error beats a stale file.
            conn2 = server._open_audit_conn()
            try:
                edited = conn2.execute(
                    "SELECT COUNT(*) FROM run_concept_facts WHERE run_id = ? "
                    "AND source = 'manual edit' AND updated_at > ?",
                    (run_id, run.ended_at),
                ).fetchone()[0]
            finally:
                conn2.close()
            if edited:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not regenerate the workbook from your edited "
                        "values. To avoid downloading a file that omits those "
                        "edits, the download was blocked. Please retry; if it "
                        "persists, check the server logs."
                    ),
                )

    # Overlay runs synchronously (openpyxl is blocking); push it off
    # the event loop so concurrent downloads don't serialise. Returns
    # the base path unchanged when notes_cells is empty, so the
    # pre-rich-editor behaviour is preserved on older runs.
    try:
        from notes.persistence import overlay_notes_cells_into_workbook
        served_path = await asyncio.to_thread(
            overlay_notes_cells_into_workbook,
            xlsx_path=base_path,
            run_id=run_id,
            db_path=str(server.AUDIT_DB_PATH),
        )
    except Exception:  # noqa: BLE001 — fall back to the base file
        logger.exception(
            "notes_cells overlay failed for run_id=%s; serving stale xlsx",
            run_id,
        )
        served_path = base_path
    # The overlay helper either returns the base path unchanged (nothing new
    # to clean up) or a fresh temp file. Track every temp file we created
    # (the re-export and/or the notes overlay) for deletion after streaming;
    # never delete the authoritative `merged_workbook_path` on disk.
    if served_path != base_path:
        temp_paths.append(Path(served_path))
    cleanup: Optional[BackgroundTask] = None
    if temp_paths:
        cleanup = BackgroundTask(_remove_overlay_tempfiles,
                                 [str(p) for p in temp_paths])
    return FileResponse(
        str(served_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"run_{run_id}_filled.xlsx",
        background=cleanup,
    )


def _remove_overlay_tempfiles(paths: list[str]) -> None:
    """Best-effort cleanup of download temp files (re-export + notes overlay)
    after the FileResponse has finished streaming. Run as a Starlette
    BackgroundTask; errors are logged but never raised — the response has
    already been sent.
    """
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove download temp file %s", path,
                         exc_info=True)


# --- Download endpoints ---

ALLOWED_DOWNLOADS = {"filled.xlsx", "result.json", "conversation_trace.json"}
# Per-statement output files are also downloadable
_STMT_PREFIXES = ("SOFP_", "SOPL_", "SOCI_", "SOCF_", "SOCIE_")


@router.get("/api/result/{session_id}/{filename}")
async def download_result(session_id: str, filename: str):
    # Reject path-traversal tokens in BOTH components. session_id is a
    # UUID in practice (see /api/upload), so `..`, `/`, `\\` are never
    # legitimate. Validating session_id matters: previously, session_id=".."
    # made session_dir the parent of OUTPUT_DIR, and the relative_to()
    # anchor below would have been computed against that malicious parent
    # — escaping the output tree entirely.
    for component in (session_id, filename):
        if ".." in component or "/" in component or "\\" in component:
            raise HTTPException(status_code=400, detail="Invalid path component.")

    # Allow per-statement files (e.g. SOFP_filled.xlsx, SOPL_result.json)
    is_stmt_file = any(filename.startswith(p) for p in _STMT_PREFIXES) and filename.endswith((".xlsx", ".json", ".txt"))
    if filename not in ALLOWED_DOWNLOADS and not is_stmt_file:
        raise HTTPException(status_code=400, detail=f"File not available. Allowed: {ALLOWED_DOWNLOADS}")

    # Belt-and-braces: anchor the resolved path under OUTPUT_DIR itself,
    # not under a session_id-derived path — otherwise a malicious
    # session_id could relocate the anchor outside OUTPUT_DIR.
    output_root = server.OUTPUT_DIR.resolve()
    try:
        file_path = (output_root / session_id / filename).resolve()
        file_path.relative_to(output_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid path component.")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(str(file_path), filename=filename)
