"""Issue reporting: who can send, read, change and delete issues and their files.

Runs with AUTH_MODE unset so real sessions and roles apply.
"""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

import server
from auth import lockout, passwords
from db import repository as repo
from db.schema import init_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "admin@firm.com", "Admin", passwords.hash_password("admin-password"))
        repo.set_auth_user_admin(conn, "admin@firm.com", True)
        repo.upsert_auth_user(conn, "ben@firm.com", "Ben", passwords.hash_password("ben-password"))
        repo.upsert_auth_user(conn, "sue@firm.com", "Sue", passwords.hash_password("sue-password"))
        run_id = repo.create_run(conn, "ABC Bhd.pdf", status="completed")
        conn.commit()
    finally:
        conn.close()
    return {"db": db, "out": tmp_path / "out", "run_id": run_id}


def _client(email: str, password: str) -> TestClient:
    client = TestClient(server.app)
    r = client.post("/api/auth/login/password", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return client


def _report(client, *, kind="wrong_figure", description="Receivables should be 1,240",
            files=(), context=None):
    return client.post(
        "/api/issues",
        data={"kind": kind, "description": description,
              "context": json.dumps(context) if context is not None else ""},
        files=[("files", f) for f in files],
    )


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def test_reporter_and_admins_can_read_an_issue_and_its_files_others_cannot(env):
    ben = _client("ben@firm.com", "ben-password")
    r = _report(ben, files=[("snip.png", PNG, "image/png"), ("note.docx", _docx(), "application/octet-stream")],
                context={"run_id": env["run_id"], "document": "ABC Bhd", "tab": "Figures"})
    assert r.status_code == 200, r.text
    issue_id = r.json()["id"]

    mine = ben.get("/api/issues").json()
    assert mine["scope"] == "own" and [i["id"] for i in mine["issues"]] == [issue_id]
    detail = ben.get(f"/api/issues/{issue_id}").json()
    assert detail["status"] == "new" and detail["run_filename"] == "ABC Bhd.pdf"
    assert detail["context"]["app_version"]  # stamped by the server, not the browser
    assert [f["content_type"] for f in detail["files"]] == [
        "image/png",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    image = ben.get(f"/api/issues/{issue_id}/files/{detail['files'][0]['id']}")
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-disposition"].startswith("inline")
    word = ben.get(f"/api/issues/{issue_id}/files/{detail['files'][1]['id']}")
    assert word.headers["content-disposition"].startswith("attachment")

    sue = _client("sue@firm.com", "sue-password")
    assert sue.get("/api/issues").json()["issues"] == []
    assert sue.get(f"/api/issues/{issue_id}").status_code == 404
    assert sue.get(f"/api/issues/{issue_id}/files/{detail['files'][0]['id']}").status_code == 404

    admin = _client("admin@firm.com", "admin-password")
    listing = admin.get("/api/issues").json()
    assert listing["scope"] == "all" and listing["counts"]["new"] == 1
    assert listing["issues"][0]["reporter_name"] == "Ben"
    assert admin.get(f"/api/issues/{issue_id}/files/{detail['files'][0]['id']}").status_code == 200


@pytest.mark.parametrize("files,data,status", [
    ([("fake.png", b"hello, not an image", "image/png")], {}, 415),
    ([("big.png", PNG + b"\x00" * (10 * 1024 * 1024), "image/png")], {}, 413),
    ([(f"{n}.png", PNG, "image/png") for n in range(6)], {}, 422),
    ([], {"description": "  "}, 422),
    ([], {"kind": "complaint"}, 422),
    *[([], {"context": {field: {"invalid": "object"}}}, 422)
      for field in ("document", "page", "tab", "statement", "path", "app_version", "browser", "window")],
    ([], {"context": {"run_id": True}}, 422),
    ([], {"context": {"run_id": "1"}}, 422),
])
def test_rejected_reports_store_nothing(env, files, data, status):
    ben = _client("ben@firm.com", "ben-password")
    r = _report(ben, files=files, **data)
    assert r.status_code == status
    assert ben.get("/api/issues").json()["issues"] == []
    assert not (env["out"] / "issues").exists() or not any((env["out"] / "issues").iterdir())


def test_only_admins_change_status_reply_or_delete_and_each_is_recorded(env):
    ben = _client("ben@firm.com", "ben-password")
    issue_id = _report(ben, files=[("snip.png", PNG, "image/png")]).json()["id"]
    update = {"status": "resolved", "admin_reply": "Fixed in the next release."}
    assert ben.patch(f"/api/issues/{issue_id}", json=update).status_code == 403
    assert ben.delete(f"/api/issues/{issue_id}").status_code == 403

    admin = _client("admin@firm.com", "admin-password")
    assert admin.patch(f"/api/issues/{issue_id}", json=update).status_code == 200
    seen = ben.get(f"/api/issues/{issue_id}").json()
    assert seen["status"] == "resolved" and seen["admin_reply"] == "Fixed in the next release."

    assert (env["out"] / "issues" / str(issue_id)).is_dir()
    assert admin.delete(f"/api/issues/{issue_id}").status_code == 200
    assert not (env["out"] / "issues" / str(issue_id)).exists()
    assert ben.get(f"/api/issues/{issue_id}").status_code == 404

    actions = [(e["action"], e["target"]) for e in admin.get("/api/admin/audit").json()["events"]]
    assert actions == [("issue_deleted", str(issue_id)), ("issue_updated", str(issue_id))]


def test_issue_outlives_its_deleted_run(env):
    ben = _client("ben@firm.com", "ben-password")
    issue_id = _report(ben, context={"run_id": env["run_id"], "document": "ABC Bhd"}).json()["id"]
    with repo.db_session(env["db"]) as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (env["run_id"],))
    detail = ben.get(f"/api/issues/{issue_id}").json()
    assert detail["run_id"] is None and detail["context"]["document"] == "ABC Bhd"
