"""ingest/pdf_sidecar.py — LLM-transcribed source sidecar for scanned PDFs.

PLAN-pdf-source-sidecar.md Phase 1. The transcriber is a pure module: rendering
+ model calls behind a monkeypatchable seam, stitching + provenance as plain
functions. No test here touches a real model.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fitz
import pytest

from ingest.pdf_sidecar import (
    SOURCE_META_NAME,
    TranscribeResult,
    normalize_transcription,
    pdf_has_text_layer,
    read_source_meta,
    source_origin_for,
    transcribe_pages,
    write_pdf_sidecar,
)


def _make_pdf(path: Path, *, pages: int = 2, with_text: bool = False) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if with_text:
            page.insert_text((72, 72), "Receivables 563,125 3,872,500")
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------- normalize


def test_normalize_strips_code_fences_and_exotic_whitespace():
    raw = "```html\n<h3>5.　Receivables</h3>\n```"
    out = normalize_transcription(raw)
    assert out == "<h3>5. Receivables</h3>"


def test_normalize_strips_presentation_but_preserves_table_geometry():
    from notes.writer import _style_cell_html

    raw = (
        '<h3 style="color: red">5 Receivables</h3>'
        '<table style="border-collapse: collapse" width="100%">'
        '<tr><th style="background-color: #ddd"><strong>Category</strong></th>'
        '<th>RM</th></tr>'
        '<tr><td rowspan="2" style="border-bottom: 3px double #000">Trade</td>'
        '<td style="text-align: right"><em>391,675</em></td></tr>'
        '<tr><td><span style="color: blue">10,000</span></td></tr>'
        '</table>'
    )

    out = normalize_transcription(raw)

    assert 'style=' not in out
    assert 'width=' not in out
    assert '<strong>' not in out
    assert '<em>' not in out
    assert '<span' not in out
    assert 'rowspan="2"' in out
    assert "Category" in out
    assert "391,675" in out
    _styled, style_source = _style_cell_html(out, None, "Receivables", [])
    assert style_source == "unstyled"


# ---------------------------------------------------------------- text layer


def test_pdf_has_text_layer_true_for_text_pdf(tmp_path):
    pdf = _make_pdf(tmp_path / "text.pdf", with_text=True)
    assert pdf_has_text_layer(pdf) is True


def test_pdf_has_text_layer_false_for_imageless_scan_stand_in(tmp_path):
    # Empty pages carry no text layer — the same signal a pure scan gives.
    pdf = _make_pdf(tmp_path / "scan.pdf", with_text=False)
    assert pdf_has_text_layer(pdf) is False


# ---------------------------------------------------------------- transcribe


def _fake_caller(fail_pages=(), fail_times=99):
    """Return (async caller, call-count dict). Fails listed pages
    ``fail_times`` times before succeeding."""
    calls: dict[int, int] = {}

    async def call(page_no: int, png_bytes: bytes) -> tuple[str, dict]:
        calls[page_no] = calls.get(page_no, 0) + 1
        if page_no in fail_pages and calls[page_no] <= fail_times:
            raise RuntimeError(f"boom page {page_no}")
        return f"<p>page {page_no}</p>", {"in": 10, "out": 5}

    return call, calls


def test_transcribe_pages_returns_html_per_page(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=3)
    call, calls = _fake_caller()
    result = asyncio.run(transcribe_pages(pdf, [1, 3], model=object(), _caller=call))
    assert isinstance(result, TranscribeResult)
    assert set(result.pages_html) == {1, 3}
    assert result.pages_html[1] == "<p>page 1</p>"
    assert result.failed_pages == []
    assert result.usage == {"in": 20, "out": 10}
    assert calls == {1: 1, 3: 1}


def test_transcribe_pages_retries_once_then_skips(tmp_path):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=3)
    call, calls = _fake_caller(fail_pages={2})
    result = asyncio.run(
        transcribe_pages(pdf, [1, 2, 3], model=object(), _caller=call)
    )
    assert result.failed_pages == [2]
    assert set(result.pages_html) == {1, 3}
    assert calls[2] == 2  # one retry, then give up — max-1-retry contract


def test_transcribe_pages_per_page_timeout_counts_as_failure(tmp_path):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=2)

    async def slow(page_no, png):
        if page_no == 1:
            await asyncio.sleep(5)
        return f"<p>page {page_no}</p>", {}

    result = asyncio.run(
        transcribe_pages(pdf, [1, 2], model=object(), _caller=slow,
                         page_timeout_s=0.05)
    )
    assert result.failed_pages == [1]
    assert set(result.pages_html) == {2}


def test_transcribe_pages_overall_deadline_fails_pending_pages(tmp_path):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=3)

    async def hang(page_no, png):
        await asyncio.sleep(30)
        return "<p>never</p>", {}

    result = asyncio.run(
        transcribe_pages(pdf, [1, 2, 3], model=object(), _caller=hang,
                         page_timeout_s=60, overall_timeout_s=0.1)
    )
    assert result.pages_html == {}
    assert result.failed_pages == [1, 2, 3]


def test_transcribe_pages_transient_failure_recovers(tmp_path):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=2)
    call, calls = _fake_caller(fail_pages={1}, fail_times=1)
    result = asyncio.run(transcribe_pages(pdf, [1, 2], model=object(), _caller=call))
    assert result.failed_pages == []
    assert set(result.pages_html) == {1, 2}
    assert calls[1] == 2


# ---------------------------------------------------------------- write


def _result(pages_html: dict[int, str], failed=()) -> TranscribeResult:
    return TranscribeResult(
        pages_html=pages_html, failed_pages=list(failed), usage={"in": 1, "out": 1}
    )


def test_write_stitches_pages_in_order_with_markers(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    out = write_pdf_sidecar(
        pdf, _result({14: "<p>fourteen</p>", 3: "<p>three</p>"}), model_name="m1"
    )
    html = out.read_text(encoding="utf-8")
    assert html.index("<p>three</p>") < html.index("<p>fourteen</p>")
    assert "<!-- pdf-page: 3 -->" in html and "<!-- pdf-page: 14 -->" in html


def test_write_refuses_to_overwrite_existing_sidecar(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    existing = tmp_path / "source.html"
    existing.write_text("<p>word original</p>", encoding="utf-8")
    out = write_pdf_sidecar(pdf, _result({1: "<p>x</p>"}), model_name="m1")
    assert out is None
    assert existing.read_text(encoding="utf-8") == "<p>word original</p>"
    assert not (tmp_path / SOURCE_META_NAME).exists()


def test_write_records_provenance_meta(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    write_pdf_sidecar(
        pdf, _result({1: "<p>x</p>", 2: "<p>y</p>"}), model_name="gpt-x"
    )
    meta = json.loads((tmp_path / SOURCE_META_NAME).read_text())
    assert meta["origin"] == "llm_transcription"
    assert meta["model"] == "gpt-x"
    assert meta["pages"] == [1, 2]
    assert meta["usage"] == {"in": 1, "out": 1}


def test_write_refuses_a_partial_transcription(tmp_path):
    """Peer review 2026-08-11 (CRITICAL): a failed MIDDLE page must not be
    silently stitched over — pages 30 and 32 joined as one apparently-complete
    note would feed the copy-verbatim channel a table missing its middle.
    All requested pages or no sidecar."""
    pdf = _make_pdf(tmp_path / "uploaded.pdf", pages=3)
    out = write_pdf_sidecar(
        pdf,
        _result({30: "<p>note start</p>", 32: "<p>note end</p>"}, failed=[31]),
        model_name="m",
    )
    assert out is None
    assert not (tmp_path / "source.html").exists()
    assert not (tmp_path / SOURCE_META_NAME).exists()


def test_write_normalizes_page_html(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    out = write_pdf_sidecar(
        pdf, _result({1: "```html\n<p>a　b</p>\n```"}), model_name="m"
    )
    assert "<p>a b</p>" in out.read_text(encoding="utf-8")
    assert "```" not in out.read_text(encoding="utf-8")


def test_write_with_zero_pages_writes_nothing(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    out = write_pdf_sidecar(pdf, _result({}, failed=[1, 2]), model_name="m")
    assert out is None
    assert not (tmp_path / "source.html").exists()
    assert not (tmp_path / SOURCE_META_NAME).exists()


# ---------------------------------------------------------------- provenance


def test_source_origin_defaults_to_docx_without_meta(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    (tmp_path / "source.html").write_text("<p>word</p>", encoding="utf-8")
    assert source_origin_for(pdf) == "docx"
    assert read_source_meta(pdf) is None


def test_source_origin_reads_meta(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    write_pdf_sidecar(pdf, _result({1: "<p>x</p>"}), model_name="m")
    assert source_origin_for(pdf) == "llm_transcription"
    assert read_source_meta(pdf)["model"] == "m"


def test_source_origin_survives_corrupt_meta(tmp_path):
    pdf = _make_pdf(tmp_path / "uploaded.pdf")
    (tmp_path / SOURCE_META_NAME).write_text("{not json", encoding="utf-8")
    assert source_origin_for(pdf) == "docx"
    assert read_source_meta(pdf) is None
