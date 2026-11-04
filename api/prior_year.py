"""Authenticated rolled-forward workbook upload and run source retrieval."""

from __future__ import annotations
from utils.paths import safe_path

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile
import tempfile
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import server
from api.mtool import _MAX_TEMPLATE_BYTES, _assert_zip_within_budget, _read_capped
from concept_model.prior_year import inspect_workbook, workbook_path
from db import repository as repo
from scout.infopack import Infopack
from utils.atomic_io import replace_with_retry, write_json_atomic

router = APIRouter()


def _session(session_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", session_id):
        raise HTTPException(status_code=404, detail="Upload session not found.")
    path = server.OUTPUT_DIR / session_id
    if not (path / "uploaded.pdf").is_file():
        raise HTTPException(status_code=404, detail="Upload session not found.")
    return path


@router.post("/api/prior-year/{session_id}")
def upload_prior_year(
    session_id: str,
    file: UploadFile = File(...),
    unit: str = Form(...),
    config: str = Form(...),
):
    session_dir = _session(session_id)
    # Stored and filled as .xlsx; a macro-enabled file under that name will not open in Excel.
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=422,
            detail="Choose a rolled-forward mTool workbook (.xlsx).",
        )
    try:
        request = server.RunConfigRequest.model_validate_json(config)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="The filing setup could not be read."
        ) from exc
    # Use the same Scout layouts the run will use, so upload and import agree.
    infopack = None
    if request.infopack:
        try:
            infopack = Infopack.from_json(json.dumps(request.infopack))
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="The saved document analysis is invalid. Run Auto-detect again, then retry.",
            ) from exc
    raw = _read_capped(file, _MAX_TEMPLATE_BYTES)
    if not raw:
        raise HTTPException(status_code=422, detail="The workbook is empty.")
    token = hashlib.sha256(raw).hexdigest()
    root = session_dir / "prior_year"
    safe_path(root).mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        path = Path(tmp) / "workbook.xlsx"
        safe_path(path).write_bytes(raw)
        _assert_zip_within_budget(str(path))
        try:
            with closing(server._open_audit_conn()) as conn:
                _, summary = inspect_workbook(conn, request.model_dump(), path, unit, infopack)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (ET.ParseError, BadZipFile) as exc:
            raise HTTPException(
                status_code=422,
                detail="The workbook contains unreadable spreadsheet data. Export it from mTool again.",
            ) from exc
        metadata = {
            "id": token,
            "filename": Path(file.filename).name,
            "unit": unit,
            "summary": summary,
        }
        # Content-addressed uploads are immutable. A reused byte-identical file
        # with another scale must not change the meaning of an existing run.
        destination = root / f"{token}.xlsx"
        if destination.exists():
            previous = json.loads(safe_path(destination.with_suffix(".json")).read_text())
            if previous["unit"] != unit:
                raise HTTPException(
                    status_code=422,
                    detail="This workbook was uploaded with another unit. Use its original unit or upload a corrected workbook.",
                )
        else:
            write_json_atomic(destination.with_suffix(".json"), metadata)
            replace_with_retry(path, destination)
    return metadata


@router.get("/api/prior-year/{session_id}/{token}")
def prior_year_metadata(session_id: str, token: str):
    try:
        path = workbook_path(_session(session_id), token)
        return json.loads(safe_path(path.with_suffix(".json")).read_text())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/runs/{run_id}/prior-year-workbook")
def run_prior_year_workbook(run_id: int):
    with closing(server._open_audit_conn()) as conn:
        run = repo.fetch_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    token = (run.config or {}).get("prior_year_workbook")
    if not token:
        raise HTTPException(
            status_code=404, detail="This run has no rolled-forward workbook."
        )
    try:
        path = workbook_path(_session(run.session_id), token)
        meta = json.loads(safe_path(path.with_suffix(".json")).read_text())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        safe_path(path),
        filename=meta["filename"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
