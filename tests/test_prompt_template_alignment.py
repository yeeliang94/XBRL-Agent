"""Prompt instructions that must stay aligned with live template coordinates."""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pytest

from prompts import render_prompt
from statement_types import StatementType


REPO = Path(__file__).resolve().parent.parent


def _date_rows(path: Path) -> set[int]:
    wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
    return {
        cell.row
        for ws in wb.worksheets
        for row in ws.iter_rows(min_row=1, max_row=min(3, ws.max_row))
        for cell in row
        if isinstance(cell.value, str) and "YYYY" in cell.value
    }


def test_shared_date_instruction_matches_company_and_group_templates():
    company_paths = [
        p for standard in ("MFRS", "MPERS")
        for p in (REPO / f"XBRL-template-{standard}" / "Company").glob("*.xlsx")
        if "SOCIE" not in p.name and not p.name.startswith("Report-")
    ]
    group_paths = [
        p for standard in ("MFRS", "MPERS")
        for p in (REPO / f"XBRL-template-{standard}" / "Group").glob("*.xlsx")
        if "SOCIE" not in p.name and not p.name.startswith("Report-")
    ]
    assert company_paths and group_paths
    assert all(_date_rows(p) == {1} for p in company_paths)
    assert all(_date_rows(p) == {2} for p in group_paths)

    prompt = render_prompt(
        StatementType.SOFP, "CuNonCu", filing_level="group",
        filing_standard="mfrs",
    )
    assert "Group non-SOCIE templates normally use B2:E2" in prompt
    assert "dates in row 1 of every sheet" not in prompt


def test_sore_prompt_matches_live_writable_and_formula_rows():
    prompt_path = REPO / "prompts" / "socie_sore.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    writable_labels = {
        "Retained earnings at beginning of period",
        "Impact of changes in accounting policies",
        "Retained earnings at beginning of period, restated",
        "Profit (loss)",
        "Dividends paid",
        "Retained earnings at end of period",
    }

    for level in ("Company", "Group"):
        path = REPO / "XBRL-template-MPERS" / level / "10-SoRE.xlsx"
        ws = openpyxl.load_workbook(path, data_only=False)["SoRE"]
        formula_rows = {
            row for row in range(1, ws.max_row + 1)
            if any(
                isinstance(ws.cell(row, col).value, str)
                and ws.cell(row, col).value.startswith("=")
                for col in range(2, ws.max_column + 1)
            )
        }
        assert formula_rows == {12, 15}
        for label in writable_labels:
            assert label in prompt

    # The former row map targeted formula/nonexistent cells after the MPERS
    # templates changed. The prompt now uses labels and live writability.
    assert not re.search(r"\brow(?:s)?\s+(12|13|14|16|17|19|20|21)\b", prompt, re.I)
    assert "closing retained-earnings\n   row is a DATA_ENTRY row" in prompt


def _words(text: str) -> set[str]:
    # Singular forms: the SSM header itself reads "Equity, others components".
    return {w.rstrip("s") for w in re.findall(r"[a-z-]+", text.lower())} - {"total", "of", "formula"}


@pytest.mark.parametrize("prompt_file,template", [
    ("socie.md", "XBRL-template-MFRS/Company/09-SOCIE.xlsx"),
    ("socie_mpers.md", "XBRL-template-MPERS/Company/09-SOCIE.xlsx"),
])
def test_socie_column_maps_match_the_template_headers(prompt_file, template):
    """Hardcoded SOCIE column maps are kept for the agent; they must not drift."""
    text = (REPO / "prompts" / prompt_file).read_text(encoding="utf-8")
    entries = [(letter, label) for letter, label in
               re.findall(r"^- ([A-Z])(?: \(\d+\))?: ([^\n(—]+)", text, re.M)
               if "evidence" not in label.lower()]  # unlabelled working column
    assert len(entries) >= 10
    ws = openpyxl.load_workbook(REPO / template, read_only=True).active
    headers = {c.column_letter: str(c.value) for c in next(ws.iter_rows(min_row=2, max_row=2)) if c.value}
    for letter, label in entries:
        expected = _words(label)
        header = headers.get(letter, "")
        assert expected <= _words(header) if expected else header.lower().strip() == label.lower().strip(), (
            f"{prompt_file} says column {letter} is '{label.strip()}', template header is '{header}'")


def test_mfrs_socie_period_row_ranges_match_the_template():
    text = (REPO / "prompts" / "socie.md").read_text(encoding="utf-8")
    ws = openpyxl.load_workbook(REPO / "XBRL-template-MFRS/Company/09-SOCIE.xlsx", read_only=True).active
    for start, end in re.findall(r"Rows (\d+)-(\d+): (?:Current|Prior) period", text):
        assert "beginning of period" in str(ws.cell(int(start), 1).value)
        assert "end of period" in str(ws.cell(int(end), 1).value)
