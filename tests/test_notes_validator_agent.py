"""Tests for the notes post-validator agent (Phase 5)."""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
from pydantic_ai.models.test import TestModel


class TestValidatorFactory:
    """Step 5.1: factory must register the 4 required tools."""

    def test_notes_validator_agent_factory_returns_agent(self, tmp_path):
        from notes.validator_agent import create_notes_validator_agent

        # Minimal merged workbook + sidecar so the factory can load inputs.
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        wb.create_sheet("Notes-SummaryofAccPol")
        wb.create_sheet("Notes-Listofnotes")
        merged_path = tmp_path / "merged.xlsx"
        wb.save(str(merged_path))

        sidecar = tmp_path / "NOTES_ACC_POLICIES_filled_payloads.json"
        sidecar.write_text(json.dumps([]), encoding="utf-8")

        agent, deps, ctx = create_notes_validator_agent(
            merged_workbook_path=str(merged_path),
            pdf_path=str(tmp_path / "x.pdf"),
            sidecar_paths=[str(sidecar)],
            filing_level="company",
            filing_standard="mfrs",
            model=TestModel(),
            output_dir=str(tmp_path),
        )
        tool_names = _agent_tool_names(agent)
        assert "view_pdf_pages" in tool_names
        assert "read_cell" in tool_names
        assert "rewrite_cell" in tool_names
        assert "flag_duplication" in tool_names


class TestDetection:
    """Steps 5.3 and 5.4: pure detection helpers."""

    def test_detects_cross_sheet_duplicate_by_note_ref(self):
        from notes.validator_agent import detect_cross_sheet_duplicates_by_ref

        entries = [
            {"sheet": "Notes-SummaryofAccPol", "row": 20, "col": 2,
             "source_note_refs": ["5.1"], "content_preview": "policy on tax"},
            {"sheet": "Notes-Listofnotes", "row": 55, "col": 2,
             "source_note_refs": ["5.1"], "content_preview": "tax reconcil"},
        ]
        dups = detect_cross_sheet_duplicates_by_ref(entries)
        assert len(dups) == 1
        assert dups[0]["note_ref"] == "5.1"

    def test_does_not_flag_same_sheet_repeats(self):
        from notes.validator_agent import detect_cross_sheet_duplicates_by_ref

        entries = [
            {"sheet": "Notes-Listofnotes", "row": 40, "col": 2,
             "source_note_refs": ["5.1"], "content_preview": "a"},
            {"sheet": "Notes-Listofnotes", "row": 112, "col": 2,
             "source_note_refs": ["5.1"], "content_preview": "a2"},
        ]
        assert detect_cross_sheet_duplicates_by_ref(entries) == []

    def test_detects_overlap_when_refs_missing(self):
        from notes.validator_agent import detect_cross_sheet_overlap_candidates

        # Same text content on both sheets, no note-refs populated.
        text = (
            "The company recognises revenue when control of the goods "
            "is transferred to the buyer, in accordance with MFRS 15."
        )
        entries = [
            {"sheet": "Notes-SummaryofAccPol", "row": 30, "col": 2,
             "source_note_refs": [], "content_preview": text},
            {"sheet": "Notes-Listofnotes", "row": 70, "col": 2,
             "source_note_refs": [], "content_preview": text},
        ]
        cands = detect_cross_sheet_overlap_candidates(entries, threshold=0.5)
        assert len(cands) == 1
        assert cands[0]["score"] >= 0.5

    def test_overlap_skips_pairs_with_matching_refs(self):
        """Overlap fallback should not duplicate what the ref-based path
        already flags (guarded by a disjoint-refs gate)."""
        from notes.validator_agent import detect_cross_sheet_overlap_candidates

        entries = [
            {"sheet": "Notes-SummaryofAccPol", "row": 10, "col": 2,
             "source_note_refs": ["7"], "content_preview": "revenue policy"},
            {"sheet": "Notes-Listofnotes", "row": 90, "col": 2,
             "source_note_refs": ["7"], "content_preview": "revenue policy"},
        ]
        assert detect_cross_sheet_overlap_candidates(entries) == []


class TestRewriteCellTool:
    """Step 5.2: `rewrite_cell` tool."""

    def test_rewrite_cell_clears_content_and_evidence(self, tmp_path):
        from notes.validator_agent import (
            NotesValidatorAgentDeps, _rewrite_cell_impl,
        )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Notes-SummaryofAccPol"
        ws.cell(row=10, column=2, value="Some prose content")
        ws.cell(row=10, column=4, value="Page 20")  # evidence col on company
        path = tmp_path / "merged.xlsx"
        wb.save(str(path))

        deps = NotesValidatorAgentDeps(
            merged_workbook_path=str(path),
            pdf_path="/tmp/x.pdf",
            sidecar_paths=[],
            filing_level="company",
            filing_standard="mfrs",
            output_dir=str(tmp_path),
            model=TestModel(),
        )
        msg = _rewrite_cell_impl(
            merged_workbook_path=str(path),
            filing_level="company",
            sheet="Notes-SummaryofAccPol",
            row=10,
            col=2,
            content="",
            evidence=None,
            deps=deps,
        )
        assert "cleared" in msg

        wb2 = openpyxl.load_workbook(str(path))
        ws2 = wb2["Notes-SummaryofAccPol"]
        assert ws2.cell(row=10, column=2).value is None
        # Evidence cleared when the primary cell was cleared.
        assert ws2.cell(row=10, column=4).value is None

    def test_rewrite_cell_refuses_formula(self, tmp_path):
        from notes.validator_agent import (
            NotesValidatorAgentDeps, _rewrite_cell_impl,
        )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Notes-SummaryofAccPol"
        ws.cell(row=5, column=2, value="=SUM(B1:B4)")
        path = tmp_path / "m.xlsx"
        wb.save(str(path))

        deps = NotesValidatorAgentDeps(
            merged_workbook_path=str(path),
            pdf_path="/tmp/x.pdf",
            sidecar_paths=[],
            filing_level="company",
            filing_standard="mfrs",
            output_dir=str(tmp_path),
            model=TestModel(),
        )
        msg = _rewrite_cell_impl(
            merged_workbook_path=str(path),
            filing_level="company",
            sheet="Notes-SummaryofAccPol",
            row=5, col=2, content="new", evidence=None,
            deps=deps,
        )
        assert "Refusing to overwrite formula cell" in msg


class TestServerHook:
    """Step 5.5: the server invokes the validator when both sheets ran."""

    @pytest.mark.asyncio
    async def test_notes_validator_hook_short_circuits_when_single_sheet(self, tmp_path):
        """Trigger condition from plan: run only when BOTH sheet 11 AND
        sheet 12 appear in the notes output."""
        import asyncio
        from server import _run_notes_validator_pass

        queue: asyncio.Queue = asyncio.Queue()
        outcome = await _run_notes_validator_pass(
            merged_workbook_path=str(tmp_path / "m.xlsx"),
            pdf_path=str(tmp_path / "x.pdf"),
            notes_template_outputs={"ACC_POLICIES": str(tmp_path / "a.xlsx")},
            filing_level="company",
            filing_standard="mfrs",
            model=TestModel(),
            output_dir=str(tmp_path),
            event_queue=queue,
        )
        assert outcome["invoked"] is False
        # No events should have been emitted on the short-circuit path.
        assert queue.empty()


def _agent_tool_names(agent) -> set[str]:
    names: set[str] = set()
    toolsets = getattr(agent, "toolsets", None) or []
    for ts in toolsets:
        tools = getattr(ts, "tools", None) or {}
        for tname in tools:
            names.add(tname)
    if names:
        return names
    legacy = getattr(agent, "_function_tools", None)
    if isinstance(legacy, dict):
        return set(legacy.keys())
    return names
