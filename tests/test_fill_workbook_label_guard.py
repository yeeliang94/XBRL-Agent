"""Bug 5b/5c — fill_workbook safety nets.

5b: if the agent submits {row: N, col: M} WITHOUT a field_label, the write
currently lands wherever row N is — even if col A at row N is blank. That's
exactly how the MPERS SOCIE bug silently wrote values to rows 30/35/49 (no
labels in col A on the MPERS Company template). The writer must reject such
writes with a clear error, preserving the row-1 date-cell carve-out that
CLAUDE.md documents in `_base.md`.

5c: MPERS Group SOCIE has 4 vertical blocks divided by uncoloured text
headers ("Group - Current period", etc.). Without registering those as
section keywords, field_label + section hint cannot pick the right block
and label-based writes on Group filings default to block 1 silently. The
section-header keyword registry must recognise them.
"""
from __future__ import annotations

import json

import openpyxl

from tools.fill_workbook import fill_workbook, _build_label_index


def _make_company_socie_like(tmp_path) -> str:
    """A minimal MPERS-Company-SOCIE-shaped workbook.

    Mirrors the real MPERS Company SOCIE: labels at rows 5, 10, 24; rows
    25-40 are empty (no col A text). Good enough to exercise the guard
    against the exact "write to row 30" bug the user hit.
    """
    path = str(tmp_path / "socie_like.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOCIE"
    ws["A5"] = "Equity at beginning of period"
    ws["A10"] = "Profit (loss)"
    ws["A24"] = "Equity at end of period"
    wb.save(path)
    wb.close()
    return path


# ---------------------------------------------------------------------------
# 5b — writer guard
# ---------------------------------------------------------------------------

class TestWriterRejectsBlankRowWrites:
    def test_rejects_row_coord_write_when_col_a_is_blank(self, tmp_path):
        template = _make_company_socie_like(tmp_path)
        output = str(tmp_path / "filled.xlsx")
        # Mirrors the MPERS bug — agent submitted {row: 30, col: 2}.
        payload = json.dumps([
            {"sheet": "SOCIE", "row": 30, "col": 2, "value": 500_000},
        ])

        result = fill_workbook(template, output, payload)

        assert result.fields_written == 0, (
            "writer wrote to a row with no col-A label — this is the exact bug"
        )
        assert any("row 30" in e.lower() for e in result.errors), (
            f"error must name the offending row. Got: {result.errors}"
        )
        # Error message should be actionable — point at the absent label.
        assert any(
            ("blank" in e.lower() or "empty" in e.lower() or "no label" in e.lower())
            for e in result.errors
        ), f"error must explain why. Got: {result.errors}"
        # S-5: the error text must not falsely claim the row doesn't exist
        # in the template — the row is there, it just has no col-A label.
        assert not any(
            "row does not exist" in e.lower() for e in result.errors
        ), (
            "error message should describe the missing LABEL, not falsely "
            f"claim the row is absent. Got: {result.errors}"
        )

    def test_allows_row_1_write_for_date_cells(self, tmp_path):
        """Carve-out: row 1 date cells have no label by design (_base.md)."""
        template = _make_company_socie_like(tmp_path)
        output = str(tmp_path / "filled.xlsx")
        payload = json.dumps([
            {"sheet": "SOCIE", "row": 1, "col": 2, "value": "01/01/2024 - 31/12/2024"},
        ])

        result = fill_workbook(template, output, payload)

        assert result.success, f"row 1 write should succeed. Errors: {result.errors}"
        assert result.fields_written == 1

    def test_field_label_writes_still_work(self, tmp_path):
        """Regression guard — normal label-based writes unchanged."""
        template = _make_company_socie_like(tmp_path)
        output = str(tmp_path / "filled.xlsx")
        payload = json.dumps([
            {"sheet": "SOCIE", "field_label": "Profit (loss)", "col": 2, "value": 322_066},
        ])

        result = fill_workbook(template, output, payload)
        assert result.success
        assert result.fields_written == 1

        wb = openpyxl.load_workbook(output)
        assert wb["SOCIE"].cell(row=10, column=2).value == 322_066
        wb.close()

    def test_explicit_row_write_still_works_when_col_a_has_label(self, tmp_path):
        """Explicit row writes are still allowed — just not on blank rows.

        Some agent patterns legitimately use row coordinates (SOCIE MFRS
        matrix is the canonical one). The guard only kicks in when col A
        at that row is genuinely empty.
        """
        template = _make_company_socie_like(tmp_path)
        output = str(tmp_path / "filled.xlsx")
        payload = json.dumps([
            {"sheet": "SOCIE", "row": 10, "col": 2, "value": 1000},
        ])

        result = fill_workbook(template, output, payload)
        assert result.success
        assert result.fields_written == 1


# ---------------------------------------------------------------------------
# 5c — MPERS Group SOCIE block-header keywords
# ---------------------------------------------------------------------------

class TestMpersGroupSocieBlockHeaders:
    def test_build_label_index_recognises_mpers_group_block_headers(self, tmp_path):
        """Build a minimal MPERS-Group-SOCIE-shaped sheet with 2 blocks.

        Each block has an identical `Profit (loss)` label; the block header
        row (e.g. "Group - Current period") must register as a section so
        the duplicates can be disambiguated.
        """
        path = str(tmp_path / "group_socie.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "SOCIE"
        # Block 1
        ws["A3"] = "Group - Current period"
        ws["A11"] = "Profit (loss)"
        # Block 2
        ws["A27"] = "Group - Prior period"
        ws["A35"] = "Profit (loss)"
        wb.save(path)
        wb.close()

        wb = openpyxl.load_workbook(path)
        idx = _build_label_index(wb)
        wb.close()

        # Both profit rows should register, each tagged with a different
        # section picked up from the block header above it.
        socie_entries = idx["SOCIE"]
        profit_entries = [e for e in socie_entries if e.normalized_label == "profit (loss)"]
        assert len(profit_entries) == 2, (
            f"expected two profit entries, one per block. Got {profit_entries}"
        )
        sections = {e.section for e in profit_entries}
        assert "group - current period" in sections, (
            f"block 1 header not registered as section. Sections: {sections}"
        )
        assert "group - prior period" in sections, (
            f"block 2 header not registered as section. Sections: {sections}"
        )

    def test_fill_workbook_disambiguates_mpers_group_blocks_by_section(self, tmp_path):
        """The happy path — same label, different block, section picks the row."""
        path = str(tmp_path / "group_socie.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "SOCIE"
        ws["A3"] = "Group - Current period"
        ws["A11"] = "Profit (loss)"
        ws["A27"] = "Group - Prior period"
        ws["A35"] = "Profit (loss)"
        wb.save(path)
        wb.close()

        output = str(tmp_path / "filled.xlsx")
        payload = json.dumps([
            {"sheet": "SOCIE", "field_label": "Profit (loss)",
             "section": "Group - Current period", "col": 2, "value": 100},
            {"sheet": "SOCIE", "field_label": "Profit (loss)",
             "section": "Group - Prior period", "col": 2, "value": 200},
        ])

        result = fill_workbook(path, output, payload)
        assert result.success, result.errors
        assert result.fields_written == 2

        wb = openpyxl.load_workbook(output)
        assert wb["SOCIE"].cell(row=11, column=2).value == 100
        assert wb["SOCIE"].cell(row=35, column=2).value == 200
        wb.close()
