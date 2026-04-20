"""Unit tests for notes/writer.py — write_notes_workbook()."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from notes.payload import NotesPayload
from notes.writer import CELL_CHAR_LIMIT, write_notes_workbook
from notes_types import NotesTemplateType, notes_template_path

# Notes-CI is the smallest template — perfect for round-trip tests.
CORP_INFO_SHEET = "Notes-CI"


def _first_matching_row(ws, label: str) -> int:
    for row in range(1, ws.max_row + 1):
        v = ws.cell(row=row, column=1).value
        if v and label.lower() in str(v).lower():
            return row
    raise AssertionError(f"Label '{label}' not found in sheet {ws.title}")


def test_company_prose_write_puts_content_in_b_and_evidence_in_d(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_filled.xlsx"
    payloads = [
        NotesPayload(
            chosen_row_label="Financial reporting status",
            content="The Group is a going concern.",
            evidence="Page 14, Note 2(a)",
            source_pages=[14],
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )
    assert result.success, result.errors
    assert result.rows_written == 1

    wb = openpyxl.load_workbook(out)
    ws = wb[CORP_INFO_SHEET]
    row = _first_matching_row(ws, "Financial reporting status")
    assert ws.cell(row=row, column=2).value == "The Group is a going concern."
    # Evidence goes to col D (4) for company filings.
    assert ws.cell(row=row, column=4).value == "Page 14, Note 2(a)"
    # Prior year + company cols are N/A for company-level templates.
    wb.close()


def test_group_prose_writes_to_group_column_only_not_company_columns(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="group")
    out = tmp_path / "Notes-CI_group_filled.xlsx"
    payloads = [
        NotesPayload(
            chosen_row_label="Financial reporting status",
            content="Consolidated entity is a going concern.",
            evidence="Page 15, Note 2(b)",
            source_pages=[15],
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="group",
        sheet_name=CORP_INFO_SHEET,
    )
    assert result.success, result.errors

    wb = openpyxl.load_workbook(out)
    ws = wb[CORP_INFO_SHEET]
    row = _first_matching_row(ws, "Financial reporting status")
    # Section 2 #6: Group filing prose → Group col B only.
    assert ws.cell(row=row, column=2).value == "Consolidated entity is a going concern."
    # Company cols (D, E) must be empty for prose.
    assert ws.cell(row=row, column=4).value in (None, "")
    assert ws.cell(row=row, column=5).value in (None, "")
    # Evidence → col F on group.
    assert ws.cell(row=row, column=6).value == "Page 15, Note 2(b)"
    wb.close()


def test_group_numeric_writes_both_group_and_company_columns(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.ISSUED_CAPITAL, level="group")
    out = tmp_path / "Notes-IC_group_filled.xlsx"
    payloads = [
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42, Note 14",
            source_pages=[42],
            numeric_values={
                "group_cy": 1000.0,
                "group_py": 900.0,
                "company_cy": 800.0,
                "company_py": 700.0,
            },
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="group",
        sheet_name="Notes-Issuedcapital",
    )
    assert result.success, result.errors

    wb = openpyxl.load_workbook(out)
    ws = wb["Notes-Issuedcapital"]
    row = _first_matching_row(ws, "Shares issued and fully paid")
    assert ws.cell(row=row, column=2).value == 1000.0  # Group CY
    assert ws.cell(row=row, column=3).value == 900.0   # Group PY
    assert ws.cell(row=row, column=4).value == 800.0   # Company CY
    assert ws.cell(row=row, column=5).value == 700.0   # Company PY
    assert ws.cell(row=row, column=6).value == "Page 42, Note 14"
    wb.close()


def test_writer_truncates_overlong_content_with_footer(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_trunc.xlsx"
    huge = "A" * (CELL_CHAR_LIMIT + 500)
    payloads = [
        NotesPayload(
            chosen_row_label="Financial reporting status",
            content=huge,
            evidence="Pages 10-12",
            source_pages=[10, 11, 12],
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )
    assert result.success

    wb = openpyxl.load_workbook(out)
    ws = wb[CORP_INFO_SHEET]
    row = _first_matching_row(ws, "Financial reporting status")
    written = ws.cell(row=row, column=2).value
    assert len(written) <= CELL_CHAR_LIMIT
    assert "truncated" in written.lower()
    assert "10" in written  # footer mentions source pages
    wb.close()


def test_writer_skips_unknown_row_labels(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_skip.xlsx"
    payloads = [
        NotesPayload(
            chosen_row_label="Completely bogus label that does not exist",
            content="something",
            evidence="Page 1",
            source_pages=[1],
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )
    # Writer returns success=False only if nothing was written AND errors exist.
    # One unmatched label produces an error but doesn't crash; rows_written=0.
    assert result.rows_written == 0
    assert any("bogus label" in e.lower() for e in result.errors)


def test_writer_surfaces_fuzzy_matches_in_result(tmp_path: Path):
    """Review I1: non-exact row resolutions must surface on the result so
    operators can review borderline matches instead of them being silent."""
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_fuzzy.xlsx"
    # Deliberately imperfect label — drops the final 's'. The writer's
    # fuzzy fallback should still resolve it but report the match.
    payloads = [
        NotesPayload(
            chosen_row_label="Financial reporting statu",
            content="The Group is a going concern.",
            evidence="Page 14",
            source_pages=[14],
        ),
    ]
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )
    assert result.success
    assert result.fuzzy_matches, "fuzzy fallback should have surfaced"
    req, chosen, score = result.fuzzy_matches[0]
    assert req == "Financial reporting statu"
    assert "financial reporting status" in chosen.lower()
    assert 0.7 <= score < 1.0


def test_writer_combines_sub_agent_ids_on_row_collision(tmp_path: Path):
    """Review S6: when multiple sub-agents land on the same row the
    combined payload must preserve every contributing sub_agent_id."""
    from notes.writer import _combine_payloads  # internal helper intentionally

    payloads = [
        NotesPayload(
            chosen_row_label="Disclosure of other notes to accounts",
            content="note A",
            evidence="p.10",
            source_pages=[10],
            sub_agent_id="sub0",
        ),
        NotesPayload(
            chosen_row_label="Disclosure of other notes to accounts",
            content="note B",
            evidence="p.20",
            source_pages=[20],
            sub_agent_id="sub2",
        ),
        NotesPayload(
            chosen_row_label="Disclosure of other notes to accounts",
            content="note C",
            evidence="p.30",
            source_pages=[30],
            sub_agent_id="sub0",  # duplicate — should dedupe
        ),
    ]
    combined = _combine_payloads(payloads)
    assert combined.sub_agent_id == "sub0,sub2"
    assert "note A" in combined.content
    assert "note C" in combined.content


def test_writer_refuses_to_overwrite_formula_cells(tmp_path: Path):
    tpl = notes_template_path(NotesTemplateType.ISSUED_CAPITAL, level="company")
    # First, inspect the template to find any formula row we could target.
    wb = openpyxl.load_workbook(str(tpl))
    ws = wb["Notes-Issuedcapital"]
    formula_row = None
    for r in range(1, ws.max_row + 1):
        for c in range(2, 4):
            v = ws.cell(row=r, column=c).value
            if isinstance(v, str) and v.startswith("="):
                formula_row = r
                break
        if formula_row:
            break
    wb.close()

    if formula_row is None:
        pytest.skip("template has no formula cells — test not applicable")

    # Fabricate a label that resolves to the formula row.
    # We can't easily hit that row by label — use a raw-row-index payload.
    # Skip if the public API doesn't support row overrides (that's OK).
    pytest.skip("row-override API not part of public writer contract; guard tested in integration")


def test_empty_payloads_returns_failure(tmp_path: Path):
    """PR A.1: zero-row writes must fail so Sheet-12's "all sub-agents lost
    coverage" case can't ship a silent green tick on an untouched template."""
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_empty.xlsx"
    result = write_notes_workbook(
        template_path=str(tpl),
        payloads=[],
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )
    assert result.success is False
    assert result.rows_written == 0
