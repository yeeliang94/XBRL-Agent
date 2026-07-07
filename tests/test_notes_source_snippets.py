"""Tests for notes.source_snippets (PLAN-word-input Phase 2 Step 8).

Snippet extraction is pure string logic over mammoth-style HTML, so most tests
build HTML directly (no docx/mammoth needed). One end-to-end test rides the
real fixture through mammoth when it's installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes import source_snippets as ss

# Hand-authored HTML in the shape mammoth emits: note headings as <h1>, prose as
# <p>, and a table under note 4.
_HTML = (
    "<h1>3. SIGNIFICANT ACCOUNTING POLICIES</h1>"
    "<p>Basis of preparation for note three.</p>"
    "<h1>4. PROPERTY, PLANT AND EQUIPMENT</h1>"
    "<p>Movement in PPE.</p>"
    "<table><tr><td>Buildings</td><td>1,595</td></tr></table>"
    "<h1>5. REVENUE</h1>"
    "<p>Revenue for note five.</p>"
)


def test_extracts_only_the_requested_note():
    snip = ss.extract_note_snippet(_HTML, 4)
    assert "PROPERTY, PLANT AND EQUIPMENT" in snip
    assert "<table>" in snip and "1,595" in snip  # note 4's table included
    assert "ACCOUNTING POLICIES" not in snip  # note 3 excluded
    assert "REVENUE" not in snip  # note 5 excluded (stops at next heading)


def test_nested_table_captured_whole():
    # Malaysian FS notes use merged cells → mammoth emits nested <table>. The
    # snippet must capture the OUTER table whole, not truncate at the inner
    # </table> (the non-greedy-regex bug). Note 5's heading must still bound it.
    html = (
        "<h1>4. PPE</h1>"
        "<table><tr><td>outer"
        "<table><tr><td>inner 42</td></tr></table>"
        "</td><td>tail 99</td></tr></table>"
        "<h1>5. REVENUE</h1><p>next note</p>"
    )
    snip = ss.extract_note_snippet(html, 4)
    assert "inner 42" in snip and "tail 99" in snip  # full outer table survived
    assert snip.count("</table>") == 2  # both closes present, balanced
    assert "next note" not in snip  # stopped at note 5, not confused by stray tags


def test_last_note_runs_to_end():
    snip = ss.extract_note_snippet(_HTML, 5)
    assert "Revenue for note five." in snip
    assert "PROPERTY" not in snip


def test_missing_note_returns_empty():
    assert ss.extract_note_snippet(_HTML, 99) == ""


def test_empty_or_none_inputs():
    assert ss.extract_note_snippet("", 4) == ""
    assert ss.extract_note_snippet(_HTML, None) == ""  # type: ignore[arg-type]


def test_note_heading_word_form():
    html = "<p>NOTE 7 - Income tax</p><p>Tax detail.</p><p>NOTE 8 - Other</p>"
    snip = ss.extract_note_snippet(html, 7)
    assert "Income tax" in snip and "Tax detail." in snip
    assert "Other" not in snip


def test_mid_paragraph_number_does_not_false_trigger():
    # "4.5%" inside prose must not be read as the start of note 4.
    html = "<p>Interest accrues at 4.5% per annum on the facility.</p>"
    assert ss.extract_note_snippet(html, 4) == ""


def test_snippet_is_capped(monkeypatch):
    monkeypatch.setattr(ss, "_SNIPPET_CHAR_CAP", 50)
    big = "<h1>4. BIG NOTE</h1><p>" + ("x" * 500) + "</p>"
    snip = ss.extract_note_snippet(big, 4)
    assert len(snip) <= 50 + len(ss._TRUNCATION_MARKER)
    assert "truncated" in snip


# --- source.html discovery helpers ---


def test_has_source_html_and_path(tmp_path: Path):
    pdf = tmp_path / "uploaded.pdf"
    pdf.write_bytes(b"%PDF")
    assert ss.has_source_html(pdf) is False
    ss.source_html_path_for(pdf).write_text(_HTML, encoding="utf-8")
    assert ss.has_source_html(pdf) is True
    assert ss.source_html_path_for(pdf) == tmp_path / "source.html"


def test_read_note_snippet_from_disk(tmp_path: Path):
    pdf = tmp_path / "uploaded.pdf"
    pdf.write_bytes(b"%PDF")
    ss.source_html_path_for(pdf).write_text(_HTML, encoding="utf-8")
    assert "1,595" in ss.read_note_snippet(pdf, 4)
    # A run dir with no source.html sidecar returns "".
    other = tmp_path / "no_sidecar"
    other.mkdir()
    assert ss.read_note_snippet(other / "uploaded.pdf", 4) == ""


def test_end_to_end_through_mammoth(tmp_path: Path):
    pytest.importorskip("mammoth")
    from ingest.docx_html import write_source_html
    from tests._docx_fixture import build_minimal_docx

    src = build_minimal_docx(tmp_path / "fs.docx")
    session = tmp_path / "sess"
    session.mkdir()
    (session / "uploaded.pdf").write_bytes(b"%PDF")
    write_source_html(src, session)
    snip = ss.read_note_snippet(session / "uploaded.pdf", 4)
    assert "1,595" in snip and "table" in snip.lower()
