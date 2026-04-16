"""Cycle 7: Download API — GET /api/result/{session_id}/{file}."""
import server
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_download_filled_excel(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess1"
    session_dir.mkdir(parents=True)
    (session_dir / "filled.xlsx").write_bytes(b"fake excel content")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/sess1/filled.xlsx")
    assert resp.status_code == 200
    assert resp.content == b"fake excel content"


def test_download_result_json(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess2"
    session_dir.mkdir(parents=True)
    (session_dir / "result.json").write_text('{"fields": []}')
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/sess2/result.json")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}


def test_download_missing_file_returns_404(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/nope/filled.xlsx")
    assert resp.status_code == 404


# Path-traversal regression. The per-statement allowlist uses startswith()+
# endswith(), which a crafted filename like "SOFP_..xlsx" satisfies. If an
# attacker can get `..` (or `/`, or `\`) into the filename parameter — whether
# through a benign-looking value, a permissive reverse proxy that preserves
# %2F, or an ASGI server that doesn't decode slashes — the endpoint must
# reject it BEFORE building the on-disk path.
def test_download_rejects_dotdot_in_filename(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    (output_dir / "sess1").mkdir(parents=True)
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/sess1/SOFP_..xlsx")
    assert resp.status_code == 400


def test_download_rejects_traversal_at_handler_level(tmp_path, monkeypatch):
    """Direct handler call: simulates an ASGI server that preserved %2F in
    the path parameter (some reverse proxies do), so filename actually
    carries `/` or `\\`. The handler itself must refuse to serve."""
    from fastapi import HTTPException
    import asyncio

    output_dir = tmp_path / "output"
    (output_dir / "sess1").mkdir(parents=True)
    (output_dir / "secret.xlsx").write_bytes(b"must not be returned")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    for malicious in (
        "SOFP_../../secret.xlsx",   # parent traversal
        "SOFP_..\\..\\secret.xlsx", # Windows-style separators
        "SOFP_/etc/passwd.xlsx",    # absolute-ish
    ):
        try:
            asyncio.get_event_loop().run_until_complete(
                server.download_result("sess1", malicious)
            )
            raise AssertionError(f"expected HTTPException for {malicious!r}")
        except HTTPException as exc:
            assert exc.status_code == 400, f"{malicious!r} returned {exc.status_code}"


def test_download_rejects_session_id_traversal(tmp_path, monkeypatch):
    """Peer-review HIGH: a malicious session_id like `..` must not be
    allowed to relocate the session directory outside OUTPUT_DIR. Plant a
    file where `OUTPUT_DIR / '..'` would resolve and confirm the handler
    refuses instead of streaming it."""
    from fastapi import HTTPException
    import asyncio

    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    # Target: sibling of OUTPUT_DIR — the place `OUTPUT_DIR / ..` escapes to.
    (tmp_path / "filled.xlsx").write_bytes(b"cross-tree secret")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    for session_id in ("..", "../other", "..\\other"):
        try:
            asyncio.get_event_loop().run_until_complete(
                server.download_result(session_id, "filled.xlsx")
            )
            raise AssertionError(f"expected HTTPException for session_id={session_id!r}")
        except HTTPException as exc:
            assert exc.status_code == 400, f"session_id={session_id!r} returned {exc.status_code}"
