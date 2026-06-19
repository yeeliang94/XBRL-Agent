"""API routes for the scanned-PDF → readable-document feature.

Standalone module (no server import), registered from server.py the same way as
concepts_routes / reviewer_routes. All routes live under /api/* so the auth
middleware already gates them (CLAUDE.md gotcha #24).

Endpoints:
  POST /api/doc-convert                  — upload a PDF, launch a conversion
  GET  /api/doc-convert/{job_id}         — status + progress (poll)
  GET  /api/doc-convert/{job_id}/events  — Server-Sent Events progress stream
  GET  /api/doc-convert/{job_id}/view    — the converted HTML (when done)
  GET  /api/doc-convert/{job_id}/download/docx — converted Word file (Phase 4)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from db import repository as repo
from .converter import (
    SUPPORTED_OCR_ENGINES,
    engine_is_bundled,
    models_bundle_dir,
    resolve_ocr_engine,
)
from .worker import run_conversion_job

logger = logging.getLogger(__name__)

# Cap matches the existing upload guard in server.py (MAX_UPLOAD_SIZE = 50 MB).
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# In-flight model downloads, by engine. A dev-only "seed from Settings"
# convenience — disk presence (engine_is_bundled) is the source of truth for
# "bundled"; this set just suppresses the spinner double-launch. Resets on
# restart, which is fine (a half-download re-checks disk on the next probe).
_model_fetch_lock = threading.Lock()
_model_fetching: set[str] = set()
# Last concrete failure message per engine, so the UI can show *why* a download
# failed (e.g. "no internet") instead of a generic message. Cleared on relaunch.
_model_fetch_errors: dict[str, str] = {}


def _run_model_fetch(engine: str) -> None:
    """Download one OCR engine's weights in the background (needs internet).

    Used by the Settings "Download models" button. On the firewalled production
    box this will fail (no network) — there models ship in the deploy artifact;
    this is a dev / online-setup convenience only.
    """
    try:
        from scripts.fetch_docling_models import fetch as _fetch

        _fetch(models_bundle_dir(), with_easyocr=(engine == "easyocr"))
        logger.info("doc-convert model fetch complete for engine=%s", engine)
        with _model_fetch_lock:
            _model_fetch_errors.pop(engine, None)
    except Exception as exc:  # noqa: BLE001 - log + persist a concrete error
        logger.warning("doc-convert model fetch failed for %s: %s", engine, exc)
        with _model_fetch_lock:
            _model_fetch_errors[engine] = str(exc) or exc.__class__.__name__
    finally:
        with _model_fetch_lock:
            _model_fetching.discard(engine)


def _safe_download_filename(original_filename: str) -> str:
    """Build a safe Content-Disposition filename from a user-supplied name.

    The uploaded filename is untrusted: reflecting it raw into a response header
    breaks on a `"` (quote injection) and on non-ASCII names (Starlette encodes
    headers as latin-1 → a `财报.pdf` would 500 the download). We emit an
    ASCII-only `filename=` plus an RFC 5987 `filename*` that preserves Unicode
    for clients that support it.
    """
    stem = Path(original_filename or "document.pdf").stem
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "document"
    ascii_name = f"{ascii_stem}-readable.docx"
    utf8_name = quote(f"{stem}-readable.docx")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"

# Terminal statuses — once a job reaches one of these the SSE stream closes.
_TERMINAL = {"done", "failed"}

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _html_to_docx_bytes(html: str) -> bytes:
    """Turn the converted HTML into a .docx via pandoc.

    Docling has no Word exporter, so we render its HTML to Word with pandoc
    (bundled in the pypandoc_binary wheel — no host install; see
    requirements.txt). pandoc writes only to a file for binary formats, so we
    round-trip through a temp file and return the bytes. Imported lazily so a
    missing dependency surfaces here, not at module load.
    """
    import tempfile

    import pypandoc

    with tempfile.TemporaryDirectory(prefix="docx_") as tmp:
        out = Path(tmp) / "out.docx"
        pypandoc.convert_text(html, "docx", format="html", outputfile=str(out))
        return out.read_bytes()


def _job_to_dict(job: "repo.DocConversion") -> dict:
    """The status payload shared by the poll endpoint and the SSE stream."""
    return {
        "job_id": job.id,
        "status": job.status,
        "current_page": job.current_page,
        "total_pages": job.total_pages,
        "original_filename": job.original_filename,
        "error": job.error,
    }


def register_doc_convert_routes(
    app,
    audit_db_getter: Callable[[], object],
    output_dir_getter: Callable[[], object],
) -> None:
    def _db_path():
        return audit_db_getter()

    def _output_dir() -> Path:
        return Path(str(output_dir_getter()))

    @app.post("/api/doc-convert")
    async def start_doc_convert(request: Request, file: UploadFile = File(...)):
        """Accept a PDF and launch a background conversion; return its job id.

        Serialised to one conversion at a time (PRD v1 decision): a second
        request while one is in flight gets a 409.
        """
        filename = file.filename or "document.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Please upload a PDF file.")

        # Reject oversize uploads from the Content-Length header BEFORE buffering
        # the whole body into memory (cheap DoS guard on the single instance).
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="PDF is too large (max 50 MB).")

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        # Defence in depth: also check the actual size (multipart overhead /
        # missing or lying Content-Length).
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="PDF is too large (max 50 MB).",
            )

        db_path = _db_path()

        # Reserve a per-job folder, then atomically create the job row IFF no
        # conversion is active (serialise to one at a time). The atomic check
        # happens BEFORE writing the uploaded bytes, so a rejected (409) upload
        # leaves no orphaned source.pdf on disk.
        job_dir = _output_dir() / "doc_conversions" / uuid.uuid4().hex
        pdf_path = job_dir / "source.pdf"
        result_html_path = job_dir / "result.html"

        job_id = repo.create_doc_conversion_if_idle(
            db_path,
            source_pdf_path=str(pdf_path),
            original_filename=filename,
        )
        if job_id is None:
            raise HTTPException(
                status_code=409,
                detail="A conversion is already in progress. Please wait "
                "for it to finish.",
            )

        # Only now persist the upload. If the write fails, fail the job (so it
        # doesn't sit queued forever) and surface a clear error.
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(data)
        except OSError as exc:
            with repo.db_session(db_path) as conn:
                repo.mark_doc_conversion_finished(
                    conn, job_id, status="failed",
                    error="Could not save the uploaded file.",
                )
            logger.warning("doc-convert upload save failed: %s", exc)
            raise HTTPException(status_code=500, detail="Could not save the upload.")

        # Launch AFTER the row is committed so the worker sees it. Daemon thread
        # so it never blocks process shutdown; a crash mid-run is reconciled at
        # the next startup.
        threading.Thread(
            target=run_conversion_job,
            args=(str(db_path), job_id, str(pdf_path), str(result_html_path)),
            name=f"doc-convert-{job_id}",
            daemon=True,
        ).start()

        return {"job_id": job_id, "status": "queued"}

    # NOTE: the static /models routes MUST be registered before the
    # /{job_id} route below — otherwise FastAPI matches "models" against the
    # int path param and 422s. Keep them here.
    @app.get("/api/doc-convert/models")
    def doc_convert_models():
        """Report which OCR engines are bundled + the current selection.

        Powers the Settings OCR-engine selector + "Download models" button.
        """
        md = models_bundle_dir()
        # Read the raw env for "current" so a stale/invalid persisted value
        # can't 500 the status endpoint; fall back to the default.
        try:
            current = resolve_ocr_engine(None)
        except Exception:  # noqa: BLE001
            current = SUPPORTED_OCR_ENGINES[0]
        return {
            "current": current,
            "engines": [
                {
                    "id": e,
                    "bundled": engine_is_bundled(md, e),
                    "fetching": e in _model_fetching,
                    "error": _model_fetch_errors.get(e),
                }
                for e in SUPPORTED_OCR_ENGINES
            ],
        }

    @app.post("/api/doc-convert/models/fetch")
    def doc_convert_fetch_models(body: dict, request: Request):
        """Launch a background download of an OCR engine's weights.

        ADMIN-ONLY: a model download is a large, networked, global side effect,
        so it's gated to admins (the dev bypass keeps it open in AUTH_MODE=dev).
        Online-only convenience (the firewalled production box ships models in
        the deploy artifact). Returns immediately; poll GET /models for state.
        """
        from .converter import DocConvertError

        # Admin gate — reuses the same server-side check as /api/admin/*.
        import sqlite3
        from auth.routes import _require_admin

        conn = sqlite3.connect(str(_db_path()))
        conn.row_factory = sqlite3.Row
        try:
            denied = _require_admin(conn, request)
        finally:
            conn.close()
        if denied is not None:
            return denied

        try:
            engine = resolve_ocr_engine(body.get("engine"))  # validates choice
        except DocConvertError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if engine_is_bundled(models_bundle_dir(), engine):
            return {"status": "already_bundled", "engine": engine}
        with _model_fetch_lock:
            # In-flight check BEFORE launching: a partial download on disk must
            # not be mistaken for "bundled" (Finding 2).
            if engine in _model_fetching:
                return {"status": "fetching", "engine": engine}
            _model_fetch_errors.pop(engine, None)  # clear stale error on relaunch
            _model_fetching.add(engine)
        threading.Thread(
            target=_run_model_fetch, args=(engine,),
            name=f"docling-fetch-{engine}", daemon=True,
        ).start()
        return {"status": "fetching", "engine": engine}

    @app.get("/api/doc-convert/{job_id}")
    def get_doc_convert(job_id: int):
        """Return the current status + progress for a conversion job."""
        with repo.db_session(_db_path()) as conn:
            job = repo.fetch_doc_conversion(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Conversion not found.")
        return _job_to_dict(job)

    @app.get("/api/doc-convert/{job_id}/events")
    async def doc_convert_events(job_id: int):
        """Stream progress as Server-Sent Events until the job is terminal.

        Decoupled from the worker: it simply tails the job row (WAL lets the
        reader see the worker's committed progress). Closes itself once the
        status is done/failed.
        """
        # Validate up front so a bad id is a clean 404, not a silent stream.
        with repo.db_session(_db_path()) as conn:
            if repo.fetch_doc_conversion(conn, job_id) is None:
                raise HTTPException(status_code=404, detail="Conversion not found.")

        async def _gen():
            # One connection for the whole stream (WAL lets it see the worker's
            # committed progress) instead of reopening every poll tick.
            with repo.db_session(_db_path()) as conn:
                last_payload = None
                while True:
                    job = repo.fetch_doc_conversion(conn, job_id)
                    if job is None:  # deleted out from under us — stop cleanly
                        break
                    payload = _job_to_dict(job)
                    # Only emit when something changed, to avoid spamming.
                    if payload != last_payload:
                        last_payload = payload
                        event = (
                            "doc_convert_complete"
                            if job.status in _TERMINAL
                            else "doc_convert_progress"
                        )
                        yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
                    if job.status in _TERMINAL:
                        break
                    await asyncio.sleep(0.5)

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.get("/api/doc-convert/{job_id}/view")
    def view_doc_convert(job_id: int):
        """Serve the converted HTML for a finished job."""
        with repo.db_session(_db_path()) as conn:
            job = repo.fetch_doc_conversion(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Conversion not found.")
        if job.status != "done" or not job.result_html_path:
            raise HTTPException(
                status_code=409,
                detail="Conversion is not finished yet.",
            )
        # Path confinement: the stored HTML must live under the output dir
        # (mirrors the trace endpoint's resolved-path check, gotcha #6).
        html_path = Path(job.result_html_path).resolve()
        out_root = _output_dir().resolve()
        if out_root not in html_path.parents:
            raise HTTPException(status_code=404, detail="Result file not found.")
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="Result file not found.")
        # Defence-in-depth for the user-derived HTML (it's OCR text, but treat it
        # as untrusted): a restrictive CSP so the document can't load scripts or
        # reach out anywhere, plus nosniff. The frontend additionally renders it
        # in a sandboxed iframe (ReadableDocPage.tsx), so these two layers mean
        # active content can neither run nor call authenticated APIs.
        return FileResponse(
            str(html_path),
            media_type="text/html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    @app.get("/api/doc-convert/{job_id}/download/docx")
    def download_doc_convert_docx(job_id: int):
        """Generate and stream the converted document as a Word (.docx) file.

        On failure we return a clear 500; the in-app HTML view stays usable
        (PRD error state) — the caller just keeps reading on screen.
        """
        with repo.db_session(_db_path()) as conn:
            job = repo.fetch_doc_conversion(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Conversion not found.")
        if job.status != "done" or not job.result_html_path:
            raise HTTPException(status_code=409, detail="Conversion is not finished yet.")

        html_path = Path(job.result_html_path).resolve()
        out_root = _output_dir().resolve()
        if out_root not in html_path.parents or not html_path.exists():
            raise HTTPException(status_code=404, detail="Result file not found.")

        try:
            docx_bytes = _html_to_docx_bytes(html_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - surface a clean error
            logger.warning("docx export failed for job %s: %s", job_id, exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Word export failed. The readable view is still available.",
            )

        # Name the download after the source PDF: "<name>-readable.docx".
        # The filename is user-supplied, so sanitize it for the header.
        return Response(
            content=docx_bytes,
            media_type=_DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": _safe_download_filename(job.original_filename)},
        )
