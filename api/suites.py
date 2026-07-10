"""Evals-workspace Suite routes (docs/PLAN-evals-workspace.md, Phase E).

A Suite is a named corpus of documents run together as a regression set. This
router is CRUD + document management; the batch RUNNER (launching + watching a
suite run) lives in ``api/suite_runner.py`` so the heavy background loop stays
out of the thin HTTP shell. Shared helpers are reached through ``server.X`` at
call time, matching the other ``api/`` routers.

Endpoints:
  GET    /api/suites                       — list suites (+ doc/run counts)
  POST   /api/suites                       — create {name}
  GET    /api/suites/{id}                  — one suite (+ its documents)
  PATCH  /api/suites/{id}                  — rename
  DELETE /api/suites/{id}                  — remove (cascades docs + suite runs)
  POST   /api/suites/{id}/docs             — add a document (upload + optional gold)
  DELETE /api/suites/{id}/docs/{doc_id}    — remove a document
  GET    /api/suites/{id}/runs             — list this suite's batch runs

Auth: the `/api/*` middleware enforces sign-in globally (gotcha #24); the Evals
workspace is open to all authenticated users, not admin-gated (decision #6).
"""
from __future__ import annotations

import logging
import shutil
import uuid as _uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import server

logger = logging.getLogger("server")

router = APIRouter()


def _suite_docs_dir() -> Path:
    """Managed storage for suite source files so a re-run months later uses
    byte-identical inputs (hybrid storage: files on disk, pointers in DB)."""
    d = server.OUTPUT_DIR / "_suite_docs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SuiteCreate(BaseModel):
    name: str


class SuiteRename(BaseModel):
    name: str


@router.get("/api/suites")
async def list_suites_endpoint():
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suites = repo.list_suites(conn)
    finally:
        conn.close()
    return {"suites": suites}


@router.post("/api/suites")
async def create_suite_endpoint(body: SuiteCreate):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the suite a name.")
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suite_id = repo.create_suite(conn, name=name)
        conn.commit()
        suite = repo.get_suite(conn, suite_id)
    finally:
        conn.close()
    return suite


@router.get("/api/suites/{suite_id}")
async def get_suite_endpoint(suite_id: int):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suite = repo.get_suite(conn, suite_id)
    finally:
        conn.close()
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    return suite


@router.patch("/api/suites/{suite_id}")
async def rename_suite_endpoint(suite_id: int, body: SuiteRename):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the suite a name.")
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        if repo.get_suite(conn, suite_id) is None:
            raise HTTPException(status_code=404, detail="Suite not found")
        repo.rename_suite(conn, suite_id, name)
        conn.commit()
        suite = repo.get_suite(conn, suite_id)
    finally:
        conn.close()
    return suite


@router.delete("/api/suites/{suite_id}")
async def delete_suite_endpoint(suite_id: int):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        removed = repo.delete_suite(conn, suite_id)
        conn.commit()
    finally:
        conn.close()
    if not removed:
        raise HTTPException(status_code=404, detail="Suite not found")
    # Best-effort: drop this suite's managed source files.
    try:
        shutil.rmtree(_suite_docs_dir() / str(suite_id), ignore_errors=True)
    except OSError:
        pass
    return {"deleted": suite_id}


@router.post("/api/suites/{suite_id}/docs")
async def add_suite_doc_endpoint(
    suite_id: int,
    file: UploadFile = File(...),
    label: str = Form(""),
    filing_standard: str = Form("mfrs"),
    filing_level: str = Form("company"),
    benchmark_id: Optional[int] = Form(None),
):
    """Add a document to a suite. The uploaded PDF/.docx is copied into managed
    storage. Optional ``benchmark_id`` attaches gold (a doc without gold still
    contributes consistency + health)."""
    if filing_standard not in ("mfrs", "mpers"):
        raise HTTPException(status_code=400, detail="filing_standard must be mfrs or mpers")
    if filing_level not in ("company", "group"):
        raise HTTPException(status_code=400, detail="filing_level must be company or group")
    fname = file.filename or ""
    if not fname.lower().endswith((".pdf", ".docx")):
        raise HTTPException(
            status_code=400,
            detail="Only .pdf or .docx documents are accepted.",
        )
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        suite = repo.get_suite(conn, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Suite not found")
        if benchmark_id is not None:
            from eval import store as _eval_store
            if _eval_store.get_benchmark(conn, benchmark_id) is None:
                raise HTTPException(status_code=404, detail="Benchmark not found")
    finally:
        conn.close()

    # Persist the file to managed storage (chunked, size-guarded).
    ext = ".docx" if fname.lower().endswith(".docx") else ".pdf"
    dest_dir = _suite_docs_dir() / str(suite_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_uuid.uuid4().hex}{ext}"
    _CHUNK = 1 * 1024 * 1024
    total = 0
    try:
        with open(dest, "wb") as fh:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > server.MAX_UPLOAD_SIZE:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size is "
                               f"{server.MAX_UPLOAD_SIZE // (1024 * 1024)}MB.",
                    )
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        logger.exception("Failed to store suite doc for suite %s", suite_id)
        raise HTTPException(status_code=500, detail="Could not store the document.")

    conn = server._open_audit_conn()
    try:
        doc_id = repo.add_suite_doc(
            conn,
            suite_id=suite_id,
            label=(label or fname).strip() or fname,
            source_path=str(dest),
            source_filename=fname,
            filing_standard=filing_standard,
            filing_level=filing_level,
            benchmark_id=benchmark_id,
        )
        conn.commit()
        suite = repo.get_suite(conn, suite_id)
    finally:
        conn.close()
    return {"doc_id": doc_id, "suite": suite}


@router.delete("/api/suites/{suite_id}/docs/{doc_id}")
async def delete_suite_doc_endpoint(suite_id: int, doc_id: int):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        docs = {d["id"]: d for d in repo.list_suite_docs(conn, suite_id)}
        doc = docs.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        repo.delete_suite_doc(conn, doc_id)
        conn.commit()
    finally:
        conn.close()
    # Best-effort file cleanup.
    try:
        if doc.get("source_path"):
            Path(doc["source_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"deleted": doc_id}


@router.get("/api/suites/{suite_id}/runs")
async def list_suite_runs_endpoint(suite_id: int):
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        if repo.get_suite(conn, suite_id) is None:
            raise HTTPException(status_code=404, detail="Suite not found")
        runs = repo.list_suite_runs(conn, suite_id)
    finally:
        conn.close()
    return {"suite_run_list": runs}
