"""Issues people report from any page, and the admin review of them.

Everyone signed in may report an issue and read their own. Admins read all,
reply, change status and delete. Attached files are stored under
``OUTPUT_DIR/issues/<id>/`` with random names and served only to the reporter
or an admin; screenshots carry client figures, so there is no public link.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

import server
from auth import config, routes as auth_routes
from db import repository as repo
from db.repository import db_session

router = APIRouter()

MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DESCRIPTION = 5000
MAX_CONTEXT_BYTES = 4000
_CHUNK = 256 * 1024

# Served inline so the detail page can show them; everything else downloads.
_INLINE_TYPES = {"image/png", "image/jpeg", "image/webp"}


class IssueUpdate(BaseModel):
    status: str
    admin_reply: str = ""


class IssueContext(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: int | None = None
    document: str | None = None
    page: str | None = None
    tab: str | None = None
    statement: str | None = None
    path: str | None = None
    app_version: str | None = None
    browser: str | None = None
    window: str | None = None


def _issues_dir() -> Path:
    return Path(server.OUTPUT_DIR) / "issues"


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _caller(conn, request: Request) -> tuple[str, bool]:
    """(email, is_admin) for the signed-in person. The middleware has already
    refused requests without a session."""
    if config.dev_bypass_active():
        return auth_routes._DEV_USER["email"], True
    email = getattr(request.state, "auth_email", "") or ""
    user = repo.fetch_auth_user(conn, email)
    return email, bool(user and user.is_admin)


def _sniff(data: bytes, filename: str) -> tuple[str, str] | None:
    """(content type, extension) judged from the file's first bytes, or None
    for anything outside the allowed list. Office files are ZIP packages, so
    the package's own parts decide Excel versus Word."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf", ".pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return None
        if "xl/workbook.xml" in names:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
        if "word/document.xml" in names:
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
    return None


async def _read_limited(upload: UploadFile) -> bytes | None:
    """Read at most MAX_FILE_BYTES; None when the file is larger."""
    buf = bytearray()
    while chunk := await upload.read(_CHUNK):
        buf.extend(chunk)
        if len(buf) > MAX_FILE_BYTES:
            return None
    return bytes(buf)


def _safe_name(name: str) -> str:
    """The original file name for display and download, path parts removed."""
    base = Path(name or "file").name.replace("\x00", "")
    return base[:200] or "file"


@router.post("/api/issues")
async def create_issue(
    request: Request,
    kind: str = Form(...),
    description: str = Form(""),
    context: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    if kind not in repo.ISSUE_KINDS:
        return _error(422, "Choose what kind of issue this is.")
    description = description.strip()
    if len(description) > MAX_DESCRIPTION:
        return _error(422, f"Keep the description under {MAX_DESCRIPTION} characters.")
    if len(files) > MAX_FILES:
        return _error(422, f"Attach up to {MAX_FILES} files.")
    if not description and not files:
        return _error(422, "Describe the issue or attach a file.")

    parsed_context = None
    if context:
        if len(context.encode()) > MAX_CONTEXT_BYTES:
            return _error(422, "Page details are too long.")
        try:
            parsed_context = IssueContext.model_validate_json(context).model_dump(exclude_none=True)
        except ValidationError:
            return _error(422, "Page details are not valid.")

    accepted: list[tuple[bytes, str, str, str]] = []
    for upload in files:
        data = await _read_limited(upload)
        name = _safe_name(upload.filename or "")
        if data is None:
            return _error(413, f"{name} is larger than 10 MB.")
        sniffed = _sniff(data, name)
        if sniffed is None:
            return _error(415, f"{name} isn't a supported type. Use PNG, JPG, WebP, PDF, Excel or Word.")
        accepted.append((data, name, *sniffed))

    # The server knows its own build; the browser does not.
    from utils.app_version import get_app_version
    parsed_context = {**(parsed_context or {}), "app_version": get_app_version()}

    run_id = parsed_context.get("run_id")

    issue_dir: Path | None = None
    try:
        with db_session(server.AUDIT_DB_PATH) as conn:
            email, _ = _caller(conn, request)
            if run_id is not None and repo.fetch_run(conn, run_id) is None:
                run_id = None
            issue_id = repo.create_issue(conn, email, kind, description, run_id, parsed_context)
            issue_dir = _issues_dir() / str(issue_id)
            issue_dir.mkdir(parents=True, exist_ok=True)
            for data, name, content_type, ext in accepted:
                stored = secrets.token_hex(16) + ext
                tmp = issue_dir / (stored + ".part")
                tmp.write_bytes(data)
                tmp.replace(issue_dir / stored)
                repo.add_issue_file(
                    conn, issue_id, stored, name, content_type, len(data),
                    hashlib.sha256(data).hexdigest(),
                )
    except Exception:
        # The row rolled back; remove any files written for it.
        if issue_dir is not None:
            shutil.rmtree(issue_dir, ignore_errors=True)
        raise
    return {"ok": True, "id": issue_id}


@router.get("/api/issues")
async def list_issues(request: Request, status: str | None = None, kind: str | None = None):
    """Admins see every issue; everyone else sees their own."""
    if status is not None and status not in repo.ISSUE_STATUSES:
        return _error(422, "Unknown status.")
    if kind is not None and kind not in repo.ISSUE_KINDS:
        return _error(422, "Unknown kind.")
    with db_session(server.AUDIT_DB_PATH) as conn:
        email, is_admin = _caller(conn, request)
        reporter = None if is_admin else email
        issues = repo.list_issues(conn, reporter_email=reporter, status=status, kind=kind)
        counts = repo.count_issues_by_status(conn, reporter_email=reporter)
    return {"issues": issues, "counts": counts, "scope": "all" if is_admin else "own"}


def _visible_issue(conn, request: Request, issue_id: int) -> tuple[dict | None, bool]:
    """The issue when the caller reported it or is an admin; otherwise None,
    so another person's issue is indistinguishable from a missing one."""
    email, is_admin = _caller(conn, request)
    issue = repo.fetch_issue(conn, issue_id)
    if issue is None or (not is_admin and issue["reporter_email"] != email.lower()):
        return None, is_admin
    return issue, is_admin


@router.get("/api/issues/{issue_id}")
async def get_issue(request: Request, issue_id: int):
    with db_session(server.AUDIT_DB_PATH) as conn:
        issue, is_admin = _visible_issue(conn, request, issue_id)
        if issue is None:
            return _error(404, "Issue not found.")
        files = repo.list_issue_files(conn, issue_id)
    issue["files"] = [
        {k: f[k] for k in ("id", "original_name", "content_type", "size_bytes")}
        for f in files
    ]
    issue["can_manage"] = is_admin
    return issue


@router.get("/api/issues/{issue_id}/files/{file_id}")
async def get_issue_file(request: Request, issue_id: int, file_id: int):
    with db_session(server.AUDIT_DB_PATH) as conn:
        issue, _ = _visible_issue(conn, request, issue_id)
        files = repo.list_issue_files(conn, issue_id) if issue is not None else []
    match = next((f for f in files if f["id"] == file_id), None)
    path = _issues_dir() / str(issue_id) / match["stored_name"] if match else None
    if match is None or not path.is_file():
        return _error(404, "File not found.")
    inline = match["content_type"] in _INLINE_TYPES
    return FileResponse(
        path,
        media_type=match["content_type"],
        filename=match["original_name"],
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.patch("/api/issues/{issue_id}")
async def update_issue(request: Request, issue_id: int, body: IssueUpdate):
    if body.status not in repo.ISSUE_STATUSES:
        return _error(422, "Unknown status.")
    reply = body.admin_reply.strip()
    if len(reply) > MAX_DESCRIPTION:
        return _error(422, f"Keep the reply under {MAX_DESCRIPTION} characters.")
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = auth_routes._require_admin(conn, request)
        if denied is not None:
            return denied
        before = repo.fetch_issue(conn, issue_id)
        if before is None:
            return _error(404, "Issue not found.")
        actor = auth_routes._actor_email(request)
        repo.update_issue(conn, issue_id, status=body.status, admin_reply=reply, updated_by=actor)
        if before["status"] != body.status or before["admin_reply"] != reply:
            repo.insert_audit_event(
                conn, actor, "issue_updated", str(issue_id),
                {"from": before["status"], "to": body.status,
                 "reply_changed": before["admin_reply"] != reply},
            )
        issue = repo.fetch_issue(conn, issue_id)
    return issue


@router.delete("/api/issues/{issue_id}")
async def delete_issue(request: Request, issue_id: int):
    """Delete an issue and its files (admin only). This is the only way issue
    files are removed."""
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = auth_routes._require_admin(conn, request)
        if denied is not None:
            return denied
        if not repo.delete_issue(conn, issue_id):
            return _error(404, "Issue not found.")
        repo.insert_audit_event(conn, auth_routes._actor_email(request), "issue_deleted", str(issue_id))
    shutil.rmtree(_issues_dir() / str(issue_id), ignore_errors=True)
    return {"ok": True}
