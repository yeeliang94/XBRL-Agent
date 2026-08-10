"""Phase 2 wiring for the transcribed source sidecar (PLAN-pdf-source-sidecar).

`server._maybe_build_pdf_sidecar` fires only when ALL of: flag on, notes
selected, scanned PDF, no existing sidecar. Failure is a structured skip,
never an exception (gotcha #20 posture). Settings round-trip mirrors
tests/test_settings_api.py.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

import server
from server import app, _maybe_build_pdf_sidecar

client = TestClient(app)


def _make_pdf(path, *, with_text=False, pages=6):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if with_text:
            page.insert_text((72, 72), "Receivables 563,125 and other text " * 3)
    doc.save(str(path))
    doc.close()
    return path


def _infopack(ranges):
    return SimpleNamespace(
        notes_inventory=[
            SimpleNamespace(page_range=r, note_num=i + 1, title=f"n{i}")
            for i, r in enumerate(ranges)
        ]
    )


NOTES = {"corporate_info"}


def _build(pdf, infopack, monkeypatch, *, fake=None, notes=NOTES):
    if fake is not None:
        import ingest.pdf_sidecar as mod
        monkeypatch.setattr(mod, "transcribe_pages", fake)
    return asyncio.run(
        _maybe_build_pdf_sidecar(str(pdf), notes, infopack, object(), "m-test")
    )


def test_flag_off_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRL_PDF_SIDECAR", raising=False)
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    assert _build(pdf, _infopack([(2, 3)]), monkeypatch) is None
    assert not (tmp_path / "source.html").exists()


def test_no_notes_selected_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    assert _build(pdf, _infopack([(2, 3)]), monkeypatch, notes=set()) is None


def test_text_layer_pdf_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf", with_text=True)
    assert _build(pdf, _infopack([(2, 3)]), monkeypatch) is None


def test_existing_word_sidecar_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    (tmp_path / "source.html").write_text("<p>word</p>", encoding="utf-8")
    assert _build(pdf, _infopack([(2, 3)]), monkeypatch) is None
    assert (tmp_path / "source.html").read_text(encoding="utf-8") == "<p>word</p>"


def test_empty_inventory_never_transcribes_blind(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    out = _build(pdf, _infopack([]), monkeypatch)
    assert out == {"status": "skipped", "reason": "no_notes_inventory"}
    assert not (tmp_path / "source.html").exists()


def test_builds_sidecar_over_inventory_page_union(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    seen = {}

    async def fake(pdf_path, pages, model, **kw):
        from ingest.pdf_sidecar import TranscribeResult
        seen["pages"] = pages
        return TranscribeResult(
            pages_html={p: f"<p>page {p}</p>" for p in pages},
            usage={"in": 100, "out": 50},
        )

    out = _build(pdf, _infopack([(2, 4), (4, 5)]), monkeypatch, fake=fake)
    assert seen["pages"] == [2, 3, 4, 5]  # union, deduped, sorted
    assert out["status"] == "built" and out["pages"] == 4
    html = (tmp_path / "source.html").read_text(encoding="utf-8")
    assert "<p>page 2</p>" in html and "<p>page 5</p>" in html
    meta = json.loads((tmp_path / "source_meta.json").read_text())
    assert meta["origin"] == "llm_transcription"
    assert meta["model"] == "m-test"


def test_page_cap_skips_degenerate_inventories(tmp_path, monkeypatch):
    """Cost guard: a page_range spanning the whole document must not fan out
    into hundreds of paid vision calls — skip loudly, never truncate."""
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    monkeypatch.setenv("XBRL_PDF_SIDECAR_PAGE_CAP", "10")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")

    async def must_not_run(*a, **kw):
        raise AssertionError("transcriber must not be called past the cap")

    out = _build(pdf, _infopack([(1, 40)]), monkeypatch, fake=must_not_run)
    assert out == {"status": "skipped", "reason": "too_many_pages",
                   "pages_requested": 40, "page_cap": 10}
    assert not (tmp_path / "source.html").exists()


def test_transcriber_exception_is_a_skip_not_a_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")
    pdf = _make_pdf(tmp_path / "uploaded.pdf")

    async def boom(*a, **kw):
        raise RuntimeError("provider down")

    out = _build(pdf, _infopack([(1, 2)]), monkeypatch, fake=boom)
    assert out["status"] == "skipped" and "provider down" in out["reason"]
    assert not (tmp_path / "source.html").exists()


def test_settings_round_trip(tmp_path, monkeypatch):
    """Default OFF; POST persists XBRL_PDF_SIDECAR; GET settings + config
    reflect it. Admin-only membership is asserted directly."""
    from api.config_routes import _ADMIN_ONLY_SETTINGS_KEYS

    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_PDF_SIDECAR", raising=False)

    assert client.get("/api/settings").json()["pdf_sidecar"] is False
    assert client.get("/api/config").json()["pdf_sidecar"] is False

    resp = client.post("/api/settings", json={"pdf_sidecar": True})
    assert resp.status_code == 200
    assert "XBRL_PDF_SIDECAR" in env_file.read_text()
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    assert client.get("/api/settings").json()["pdf_sidecar"] is True
    assert server._pdf_sidecar_enabled() is True

    assert "pdf_sidecar" in _ADMIN_ONLY_SETTINGS_KEYS
