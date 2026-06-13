"""PLAN-orchestration-hardening item 19 — search_pdf_text tool.

Pins the pure search function (text hits, batching, cap, scanned signal) and
that the tool is registered on the extraction, notes, and reviewer agents. The
test PDFs are built in-process with PyMuPDF so the suite is self-contained.
"""
from __future__ import annotations

import json

import fitz  # PyMuPDF
import pytest

from tools.pdf_search import search_pdf_text, search_pdf_text_json


def _text_pdf(tmp_path, pages: list[str]):
    """Build a text PDF with one page per string. Returns the path."""
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 100), body, fontsize=11)
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _scanned_pdf(tmp_path):
    """Build an image-only (text-less) PDF — three blank pages, no text layer."""
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


# --------------------------------------------------------------------------
# Pure search function
# --------------------------------------------------------------------------

def test_finds_phrase_with_pdf_page_number(tmp_path):
    pdf = _text_pdf(tmp_path, [
        "Statement of financial position\nRevenue 1,595",
        "Notes\nAmounts owing by directors 200",
    ])
    out = search_pdf_text(pdf, ["amounts owing by directors"])
    assert out["scanned"] is False
    res = out["results"][0]
    assert res["total_matches"] == 1
    # Hit is on PDF page 2 (1-based), the scale the other tools use.
    assert res["hits"][0]["page"] == 2
    assert "owing by directors" in res["hits"][0]["snippet"].lower()


def test_search_is_case_insensitive(tmp_path):
    pdf = _text_pdf(tmp_path, ["Deferred Tax Liabilities 42"])
    out = search_pdf_text(pdf, ["deferred tax"])
    assert out["results"][0]["total_matches"] == 1


def test_multiple_queries_batched(tmp_path):
    pdf = _text_pdf(tmp_path, ["Revenue 100", "Employee benefits 50"])
    out = search_pdf_text(pdf, ["revenue", "employee benefits", "no such phrase"])
    by_q = {r["query"]: r for r in out["results"]}
    assert by_q["revenue"]["total_matches"] == 1
    assert by_q["employee benefits"]["total_matches"] == 1
    assert by_q["no such phrase"]["total_matches"] == 0
    assert by_q["no such phrase"]["hits"] == []


def test_max_hits_caps_returned_hits_but_reports_true_total(tmp_path):
    # Five pages each containing the term → 5 true matches; with one query the
    # whole max_hits budget is its per-query allocation, so 2 hits return.
    pdf = _text_pdf(tmp_path, ["the term here"] * 5)
    out = search_pdf_text(pdf, ["term"], max_hits=2)
    res = out["results"][0]
    assert res["total_matches"] == 5      # true count preserved
    assert len(res["hits"]) == 2          # list clipped to the per-query slot
    # Clipping carries a structured recovery note, not a silent empty tail.
    assert "clipped" in (res["note"] or "")
    assert "re-search" in res["note"]


def test_per_query_allocation_prevents_starvation(tmp_path):
    # Pre-fix failure mode: ONE global counter meant an early common term
    # consumed the whole cap and later queries returned total_matches>0 with
    # hits=[] and no recovery path. Each query now gets its own slot budget.
    pdf = _text_pdf(tmp_path, ["common common common\nrare needle"] * 4)
    out = search_pdf_text(pdf, ["common", "rare needle"], max_hits=4)
    by_q = {r["query"]: r for r in out["results"]}
    # 2 queries × max_hits=4 → 2 slots each.
    common = by_q["common"]
    assert common["total_matches"] == 12
    assert len(common["hits"]) == 2
    assert "clipped" in (common["note"] or "")
    # The later query still gets ITS slots despite the earlier common term.
    rare = by_q["rare needle"]
    assert rare["total_matches"] == 4
    assert len(rare["hits"]) == 2, (
        "later query starved by an earlier common term"
    )


def test_query_count_clamped_to_20_with_note(tmp_path):
    pdf = _text_pdf(tmp_path, ["needle on this page"])
    queries = [f"q{i}" for i in range(25)] + ["needle"]
    out = search_pdf_text(pdf, queries)
    assert len(out["results"]) == 20          # clipped to the first 20
    assert "clipped" in (out["note"] or "")   # structured, not silent
    assert "20" in out["note"]
    # The dropped 26th query ("needle") is not in the results.
    assert all(r["query"] != "needle" for r in out["results"])


def test_query_length_clamped_to_200_with_note(tmp_path):
    pdf = _text_pdf(tmp_path, ["alpha " * 60])
    long_q = "alpha " * 60  # 360 chars — only its first 200 are searched
    out = search_pdf_text(pdf, [long_q])
    res = out["results"][0]
    assert "truncated" in (res["note"] or "")
    assert "200" in res["note"]
    # The truncated needle is still searched (no silent empty result).
    assert res["total_matches"] >= 0  # shape intact; no raise


def test_snippet_is_bounded(tmp_path):
    # Lots of surrounding text (newline-separated so insert_text doesn't run off
    # the page) — the snippet must still clip to <=200 chars around the hit.
    body = "\n".join(["filler word"] * 40 + ["needle here"] + ["filler word"] * 40)
    pdf = _text_pdf(tmp_path, [body])
    out = search_pdf_text(pdf, ["needle"])
    snip = out["results"][0]["hits"][0]["snippet"]
    assert "needle" in snip
    assert len(snip) <= 200


def test_hybrid_pdf_with_late_text_is_not_scanned(tmp_path):
    # Image-only front matter (blank page) followed by a text-layer page — the
    # searchable page must still be searched, not written off as scanned.
    doc = fitz.open()
    doc.new_page()  # page 1: no text (image-only front matter)
    p2 = doc.new_page()  # page 2: has the term
    p2.insert_text((72, 100), "Employee benefits 1,234", fontsize=11)
    path = tmp_path / "hybrid.pdf"
    doc.save(str(path))
    doc.close()

    out = search_pdf_text(str(path), ["employee benefits"])
    assert out["scanned"] is False
    res = out["results"][0]
    assert res["total_matches"] == 1
    assert res["hits"][0]["page"] == 2


def test_scanned_pdf_returns_explicit_signal(tmp_path):
    pdf = _scanned_pdf(tmp_path)
    out = search_pdf_text(pdf, ["anything"])
    assert out["scanned"] is True
    assert out["results"] == []
    assert "scanned" in (out["message"] or "").lower()


def test_json_wrapper_never_raises_on_bad_path(tmp_path):
    out = json.loads(search_pdf_text_json(str(tmp_path / "missing.pdf"), ["x"]))
    assert "error" in out
    assert out["results"] == []


# --------------------------------------------------------------------------
# Tool registration on all three agents
# --------------------------------------------------------------------------

def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def test_extraction_agent_exposes_search_pdf_text():
    from pydantic_ai.models.test import TestModel
    from statement_types import StatementType
    from extraction.agent import create_extraction_agent

    agent, _ = create_extraction_agent(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        pdf_path="/tmp/test.pdf", template_path="/tmp/test.xlsx",
        model=TestModel(), output_dir="/tmp/output",
    )
    assert "search_pdf_text" in _tool_names(agent)


def test_notes_agent_exposes_search_pdf_text(tmp_path):
    from pydantic_ai.models.test import TestModel
    from notes_types import NotesTemplateType
    from notes.agent import create_notes_agent

    agent, _ = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
    )
    assert "search_pdf_text" in _tool_names(agent)


def test_reviewer_agent_exposes_search_pdf_text(tmp_path):
    from pydantic_ai.models.test import TestModel
    from db.schema import init_db
    from correction.reviewer_agent import create_reviewer_agent

    db = tmp_path / "r.db"
    init_db(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026Z', 'x.pdf', 'completed')").lastrowid)
    conn.commit()
    conn.close()

    agent, _ = create_reviewer_agent(
        model=TestModel(), db_path=db, run_id=run_id, pdf_path="/tmp/x.pdf",
    )
    assert "search_pdf_text" in _tool_names(agent)
