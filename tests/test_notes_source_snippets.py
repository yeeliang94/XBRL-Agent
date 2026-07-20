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


def test_injected_cell_styles_survive_the_slice():
    """Step 6: source.html now carries real Word styling on table cells
    (ingest.docx_html injection). The slicer returns note content verbatim, so
    those style= attributes must ride along into the per-note chunk — otherwise
    the agent is back to copying a bare skeleton."""
    styled = (
        "<h1>4. PROPERTY, PLANT AND EQUIPMENT</h1>"
        "<p>Movement in PPE.</p>"
        '<table><tr>'
        '<td style="text-align: right">Buildings</td>'
        '<td style="border-bottom: 3px double #000000; text-align: right">'
        "1,595</td></tr></table>"
        "<h1>5. REVENUE</h1><p>Revenue.</p>"
    )
    snip = ss.extract_note_snippet(styled, 4)
    assert "3px double #000000" in snip
    assert "text-align: right" in snip
    assert len(snip) <= ss._SNIPPET_CHAR_CAP


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


def test_whitespace_only_heading_in_heading_tag():
    # Word-styled headings often have no punctuation: "4 Property, plant...".
    # mammoth renders them as <h1>, where the looser rule applies.
    html = (
        "<h1>4 PROPERTY, PLANT AND EQUIPMENT</h1><p>PPE detail.</p>"
        "<h1>5 REVENUE</h1><p>Revenue detail.</p>"
    )
    snip = ss.extract_note_snippet(html, 4)
    assert "PROPERTY" in snip and "PPE detail." in snip
    assert "REVENUE" not in snip  # bounded by the note-5 heading


def test_prose_paragraph_starting_with_number_is_not_a_boundary():
    # A <p> (not a heading) that opens with a bare number must NOT be read as a
    # note heading — otherwise "12 months ended..." inside note 4 would split it.
    assert ss._heading_note_num("<p>12 months ended 31 December 2024.</p>") is None
    # But the same number as a styled heading IS a boundary.
    assert ss._heading_note_num("<h2>12 Income tax</h2>") == 12
    # Punctuated prose headings still work (e.g. bolded "4. Revenue" as <p>).
    assert ss._heading_note_num("<p>4. Revenue</p>") == 4


def test_prose_number_start_does_not_split_a_note():
    html = (
        "<h1>4 LEASES</h1>"
        "<p>12 months of lease payments were made during the year.</p>"
        "<h1>5 REVENUE</h1><p>x</p>"
    )
    snip = ss.extract_note_snippet(html, 4)
    assert "12 months of lease payments" in snip  # prose stayed inside note 4
    assert "REVENUE" not in snip


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


def test_first_block_alone_over_cap_is_hard_cut(monkeypatch):
    # A whole note rendered as one giant <table> (a common Malaysian-FS shape:
    # the entire note IS a single table whose text opens "4. Revenue …") makes
    # that table the note's heading block. It alone exceeds the cap and there's
    # no block boundary to stop at, so it must be hard-cut — NOT returned
    # uncapped. Regression guard: the cap loop unconditionally accepts the first
    # block, so without the explicit pre-loop guard this returned the full note.
    monkeypatch.setattr(ss, "_SNIPPET_CHAR_CAP", 60)
    giant = (
        "<table><tr><td>4. Revenue</td></tr>"
        + ("<tr><td>x</td></tr>" * 50)
        + "</table>"
    )
    assert len(giant) > 60
    snip = ss.extract_note_snippet(giant, 4)
    assert len(snip) <= 60 + len(ss._TRUNCATION_MARKER)
    assert "truncated" in snip


def test_decimal_prose_not_read_as_note_heading():
    # "4.5% of receivables were impaired" must NOT parse as the Note 4 heading —
    # without the strict rule's (?!\d), the decimal splits as number-4 + "."
    # separator and mis-anchors the note. Verified against the real bug shape.
    assert ss._heading_note_num("<p>4.5% of receivables were impaired.</p>") is None
    # A genuine "4." prose heading still resolves.
    assert ss._heading_note_num("<p>4. Revenue</p>") == 4


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


# --- table-of-contents immunity (2026-07-19) --------------------------------
# Word financial statements open with a contents list whose entries look
# exactly like note headings ("1.  Corporate information<tab>6"). Before the
# fix the slicer matched the FIRST such block, so every note resolved to its
# one-line TOC entry (no table, no formatting) and the last TOC entry
# swallowed everything up to the first real heading. Measured on
# data/FINCO-Audited-Financial-Statement-2021.docx: 15 of 15 notes wrong.

_TOC_HTML = """
<h1>Contents</h1>
<p>1.  Corporate information\t6</p>
<p>2.  Significant accounting policies\t6</p>
<p>3.  Office equipment\t14</p>
<h2>1.\tCorporate information</h2>
<p>The company is incorporated in Malaysia.</p>
<h2>2.\tSignificant accounting policies</h2>
<p>Prepared under MFRS.</p>
<h2>3.\tOffice equipment</h2>
<table><tr><td style="padding: 1px 5px">Cost</td><td>1,595</td></tr></table>
"""


def test_toc_entries_are_not_mistaken_for_note_headings():
    """The real <h2> note wins over the earlier TOC <p> with the same number."""
    snip = ss.extract_note_snippet(_TOC_HTML, 1)
    assert "incorporated in Malaysia" in snip
    assert "Corporate information\t6" not in snip


def test_toc_does_not_swallow_the_body_into_the_last_entry():
    """The final TOC line must not absorb everything up to the first real note."""
    snip = ss.extract_note_snippet(_TOC_HTML, 3)
    assert "<table" in snip
    assert "1,595" in snip
    # Must be note 3's own table, not the whole document body.
    assert "incorporated in Malaysia" not in snip


def test_note_snippet_keeps_word_cell_styling():
    """Verbatim passthrough depends on cell style= reaching the agent intact."""
    snip = ss.extract_note_snippet(_TOC_HTML, 3)
    assert "padding: 1px 5px" in snip


def test_dot_leader_toc_entries_are_also_refused():
    """Word contents lists use either a tab or a dot leader before the page
    number; only the tab form was covered before (code review 2026-07-19)."""
    html = (
        "<p>1.  Corporate information......6</p>"
        "<p>2.  Receivables.........14</p>"
        "<h2>1.\tCorporate information</h2>"
        "<p>Real body text.</p>"
    )
    snip = ss.extract_note_snippet(html, 1)
    assert "Real body text" in snip
    assert "......6" not in snip


def test_a_heading_ending_in_a_year_is_not_treated_as_a_toc_line():
    """The tab must be immediately followed by the page digits; a heading whose
    TITLE ends in a number must still resolve."""
    html = (
        "<h2>5.\tRevenue for the year ended 31 December 2021</h2>"
        "<p>Body.</p>"
    )
    assert "Body." in ss.extract_note_snippet(html, 5)
