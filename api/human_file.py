"""Human-filled mTool file routes (docs/human-mtool-file-comparison-plan.md).

Endpoints:
  ``POST   /api/runs/{run_id}/human-file`` — attach or replace the mTool file a
        person filled for the same document; the form carries its ``unit``
  ``GET    /api/runs/{run_id}/human-file`` — the attached file record, or null
  ``DELETE /api/runs/{run_id}/human-file`` — remove it
  ``GET    /api/runs/{run_id}/human-comparison`` — the comparison with the
        run's current values

Any signed-in user may call these; the auth middleware guards ``/api/*``
(gotcha #24). The upload is read into the database and then discarded.
"""
from __future__ import annotations

import tempfile
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

import server
from api.mtool import (
    _MAX_TEMPLATE_BYTES,
    _assert_zip_within_budget,
    _operator_label,
    _read_capped,
)
from eval.human_compare import load_comparison
from eval.human_file import (
    HumanFileError,
    delete_rows,
    ingest_human_file,
    load_human_file,
)

router = APIRouter()

_ALLOWED_SUFFIXES = (".xlsx", ".xlsm")


def _conn():
    # Shared pragmas, including foreign keys: a run deleted mid-upload must
    # make the insert fail rather than leave an orphaned file record.
    return server._open_audit_conn()


@router.post("/api/runs/{run_id}/human-file")
def attach_human_file(
    request: Request,
    run_id: int,
    file: UploadFile = File(...),
    unit: str = Form(...),
):
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(_ALLOWED_SUFFIXES):
        raise HTTPException(status_code=422, detail="Choose the mTool .xlsx file.")
    raw = _read_capped(file, _MAX_TEMPLATE_BYTES)
    if not raw:
        raise HTTPException(status_code=422, detail="This file is empty.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "human.xlsx"
        path.write_bytes(raw)
        _assert_zip_within_budget(str(path))
        with closing(_conn()) as conn:
            try:
                record = ingest_human_file(
                    conn, run_id, path, filename=filename, unit=unit,
                    uploaded_by=_operator_label(request))
            except HumanFileError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"file": record}


@router.get("/api/runs/{run_id}/human-file")
def get_human_file(run_id: int):
    with closing(_conn()) as conn:
        return {"file": load_human_file(conn, run_id)}


@router.delete("/api/runs/{run_id}/human-file")
def remove_human_file(run_id: int):
    with closing(_conn()) as conn, conn:
        if not delete_rows(conn, run_id):
            raise HTTPException(status_code=404, detail="This run has no human file.")
    return {"deleted": True}


@router.get("/api/runs/{run_id}/human-comparison")
def get_human_comparison(run_id: int):
    """Recomputed on every read from the run's current facts and notes."""
    with closing(_conn()) as conn:
        result = load_comparison(conn, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="This run has no human file.")
    return result
