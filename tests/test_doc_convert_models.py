"""OCR-engine selection + model-seeding for the readable-doc feature.

Covers converter engine resolution, the /api/doc-convert/models status +
fetch endpoints, and the docling_ocr_engine settings round-trip. conftest
defaults AUTH_MODE=dev so the /api/* routes don't 401.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.schema import init_db
from docconvert import converter as conv


# --- converter-level unit tests (no server) --------------------------------

def test_resolve_ocr_engine_precedence(monkeypatch):
    assert conv.resolve_ocr_engine(None) == "rapidocr"  # default
    assert conv.resolve_ocr_engine("EasyOCR") == "easyocr"  # arg, normalised
    monkeypatch.setenv("XBRL_DOCLING_OCR_ENGINE", "easyocr")
    assert conv.resolve_ocr_engine(None) == "easyocr"  # env
    assert conv.resolve_ocr_engine("rapidocr") == "rapidocr"  # arg beats env


def test_resolve_ocr_engine_rejects_unknown():
    with pytest.raises(conv.DocConvertError):
        conv.resolve_ocr_engine("tesseract-9000")


def test_engine_is_bundled_requires_complete_set(tmp_path):
    # A partial download (only the detector) must NOT report bundled.
    assert conv.engine_is_bundled(tmp_path, "rapidocr") is False
    onnx = tmp_path / "RapidOcr" / "onnx"
    onnx.mkdir(parents=True)
    (onnx / "ch_det_mobile.onnx").write_bytes(b"x")
    assert conv.engine_is_bundled(tmp_path, "rapidocr") is False  # only det
    (onnx / "ch_cls_mobile.onnx").write_bytes(b"x")
    assert conv.engine_is_bundled(tmp_path, "rapidocr") is False  # det + cls
    (onnx / "en_rec_mobile.onnx").write_bytes(b"x")
    assert conv.engine_is_bundled(tmp_path, "rapidocr") is True   # full set


def test_engine_is_bundled_easyocr_complete_set(tmp_path):
    root = tmp_path / "EasyOcr"
    root.mkdir()
    assert conv.engine_is_bundled(tmp_path, "easyocr") is False
    (root / "craft_mlt_25k.pth").write_bytes(b"x")
    assert conv.engine_is_bundled(tmp_path, "easyocr") is False  # detector only
    (root / "english_g2.pth").write_bytes(b"x")
    assert conv.engine_is_bundled(tmp_path, "easyocr") is True   # + recognizer


def test_build_converter_easyocr_missing_package_error(tmp_path, monkeypatch):
    # Simulate a stale env without the easyocr package → clear, actionable error
    # (not a deep docling ImportError at convert time).
    import builtins

    real_import = builtins.__import__

    def _no_easyocr(name, *a, **k):
        if name == "easyocr":
            raise ImportError("No module named 'easyocr'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_easyocr)
    with pytest.raises(conv.DocConvertError) as exc:
        conv._build_converter(tmp_path, "easyocr")
    assert "easyocr" in str(exc.value).lower()


def test_build_converter_easyocr_missing_models_raises(tmp_path):
    # rapidocr present, easyocr absent → selecting easyocr errors clearly.
    (tmp_path / "RapidOcr").mkdir()
    with pytest.raises(conv.DocConvertError) as exc:
        conv._build_converter(tmp_path, "easyocr")
    assert "easyocr" in str(exc.value).lower()


# --- route-level tests ------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    # Point the model bundle at a throwaway dir so disk probes are deterministic.
    monkeypatch.setenv("DOCLING_MODELS_DIR", str(tmp_path / "models"))
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    # Isolate the .env writes (POST /api/settings) to a tmp file so the test
    # doesn't pollute the repo .env (mirrors tests/test_settings.py).
    srv.ENV_FILE = tmp_path / ".env"
    srv.ENV_FILE.write_text("", encoding="utf-8")
    init_db(db)
    return TestClient(srv.app)


def test_models_endpoint_reports_bundle_status(client, tmp_path):
    resp = client.get("/api/doc-convert/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == "rapidocr"
    ids = {e["id"]: e for e in body["engines"]}
    assert set(ids) == {"rapidocr", "easyocr"}
    # The throwaway bundle dir is empty → nothing bundled.
    assert ids["rapidocr"]["bundled"] is False
    assert ids["easyocr"]["bundled"] is False


def test_fetch_models_validates_engine(client):
    assert client.post("/api/doc-convert/models/fetch", json={"engine": "nope"}).status_code == 400


def test_fetch_models_already_bundled_short_circuits(client, tmp_path):
    # Seed a COMPLETE rapidocr bundle so fetch reports already_bundled (no net).
    onnx = tmp_path / "models" / "RapidOcr" / "onnx"
    onnx.mkdir(parents=True)
    for name in ("ch_det.onnx", "ch_cls.onnx", "en_rec.onnx"):
        (onnx / name).write_bytes(b"x")
    resp = client.post("/api/doc-convert/models/fetch", json={"engine": "rapidocr"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_bundled"


def test_fetch_models_partial_bundle_does_not_short_circuit(client, tmp_path, monkeypatch):
    # Only the detector present → NOT already_bundled; a fetch would launch.
    # Stub the background fetch so the test doesn't hit the network.
    onnx = tmp_path / "models" / "RapidOcr" / "onnx"
    onnx.mkdir(parents=True)
    (onnx / "ch_det.onnx").write_bytes(b"x")  # partial only
    import docconvert.routes as routes
    monkeypatch.setattr(routes, "_run_model_fetch", lambda engine: None)
    resp = client.post("/api/doc-convert/models/fetch", json={"engine": "rapidocr"})
    assert resp.json()["status"] == "fetching"  # not "already_bundled"


def test_real_easyocr_initialises(tmp_path):
    """The bundled EasyOCR path actually builds a Docling converter (no error).

    Guards against the 'EasyOCR selectable but undeclared dependency' finding —
    if easyocr weren't importable this would raise. Skips when the EasyOCR
    bundle isn't present locally.
    """
    bundle = conv.models_bundle_dir()
    if not conv.engine_is_bundled(bundle, "easyocr"):
        pytest.skip("EasyOCR models not bundled locally")
    # Should construct without raising (proves easyocr is importable + wired).
    conv._build_converter(bundle, "easyocr")


def test_fetch_models_is_admin_gated(tmp_path, monkeypatch):
    """A non-admin must NOT be able to launch a global model download.

    Runs with AUTH_MODE unset so real sessions apply (mirrors test_admin_routes).
    """
    import sqlite3

    import server
    from auth import lockout, passwords
    from db import repository as repo

    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "admin@firm.com", "Admin", passwords.hash_password("admin-password"))
        repo.set_auth_user_admin(conn, "admin@firm.com", True)
        repo.upsert_auth_user(conn, "user@firm.com", "User", passwords.hash_password("user-password"))
        conn.commit()
    finally:
        conn.close()

    c = TestClient(server.app)
    # Unauthenticated → 401.
    assert c.post("/api/doc-convert/models/fetch", json={"engine": "easyocr"}).status_code == 401
    # Non-admin → 403.
    assert c.post("/api/auth/login/password",
                  json={"email": "user@firm.com", "password": "user-password"}).status_code == 200
    assert c.post("/api/doc-convert/models/fetch", json={"engine": "easyocr"}).status_code == 403


def test_settings_round_trip_docling_ocr_engine(client):
    # GET exposes the default; POST persists a valid value; invalid → 400.
    assert client.get("/api/settings").json()["docling_ocr_engine"] == "rapidocr"
    assert client.post("/api/settings", json={"docling_ocr_engine": "easyocr"}).status_code == 200
    assert client.get("/api/settings").json()["docling_ocr_engine"] == "easyocr"
    assert client.post("/api/settings", json={"docling_ocr_engine": "bogus"}).status_code == 400
