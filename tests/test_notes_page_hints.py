"""Regression tests for the notes page-hints plumbing.

On scanned PDFs scout's deterministic notes-inventory builder returns
empty (no extractable text for the header regex), which used to leave
notes agents with no guidance and trigger full-document sweeps. These
tests pin the union-derivation on ``Infopack.notes_page_hints()`` and
the prompt-rendering behaviour on ``render_notes_prompt``, plus verify
``NotesRunConfig``/``run_notes_extraction`` accept and forward the list.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from notes.agent import _render_page_hints_block, render_notes_prompt
from notes.coordinator import NotesRunConfig, run_notes_extraction
from notes_types import NotesTemplateType
from scout.infopack import Infopack, StatementPageRef
from statement_types import StatementType


# ---------------------------------------------------------------------------
# Infopack.notes_page_hints
# ---------------------------------------------------------------------------

def test_empty_infopack_returns_empty_hints():
    ip = Infopack(toc_page=2, page_offset=2)
    assert ip.notes_page_hints() == []


def test_infopack_unions_face_and_note_pages_sorted_unique():
    ip = Infopack(
        toc_page=2,
        page_offset=2,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=14,
                note_pages=[28, 29, 30],
                confidence="HIGH",
            ),
            StatementType.SOPL: StatementPageRef(
                variant_suggestion="Nature",
                face_page=15,
                note_pages=[33],
                confidence="HIGH",
            ),
            StatementType.SOCI: StatementPageRef(
                # Overlaps with SOPL face_page on the same physical page -
                # must dedupe.
                variant_suggestion="BeforeTax",
                face_page=15,
                note_pages=[33],
                confidence="HIGH",
            ),
            StatementType.SOCF: StatementPageRef(
                variant_suggestion="Direct",
                face_page=17,
                note_pages=[],
                confidence="HIGH",
            ),
        },
    )
    # Sorted, deduped, every page appears exactly once.
    assert ip.notes_page_hints() == [14, 15, 17, 28, 29, 30, 33]


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_prompt_includes_hint_block_when_hints_present():
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
        page_hints=[14, 15, 18, 33],
    )
    assert "SUGGESTED STARTING PAGES" in prompt
    assert "14, 15, 18, 33" in prompt
    # The instruction against blanket page-1-N sweeps must be present —
    # this was the observed failure mode in production runs.
    assert "Do NOT sweep" in prompt or "do not sweep" in prompt.lower()


def test_prompt_omits_hint_block_when_hints_empty():
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
        page_hints=[],
    )
    assert "SUGGESTED STARTING PAGES" not in prompt


def test_prompt_omits_hint_block_when_hints_none():
    """Default-None arg (back-compat for existing callers) → no hint block."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )
    assert "SUGGESTED STARTING PAGES" not in prompt


def test_render_page_hints_block_is_none_when_empty():
    assert _render_page_hints_block([]) is None


def test_render_page_hints_block_formats_pages_inline():
    block = _render_page_hints_block([14, 15, 28])
    assert block is not None
    assert "14, 15, 28" in block


# ---------------------------------------------------------------------------
# Coordinator wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coordinator_derives_hints_from_infopack(tmp_path):
    """Config with default (empty) page_hints inherits the infopack's union."""
    ip = Infopack(
        toc_page=2, page_offset=2,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu", face_page=14,
                note_pages=[28, 29], confidence="HIGH",
            ),
        },
    )
    config = NotesRunConfig(
        pdf_path=str(tmp_path / "x.pdf"),
        output_dir=str(tmp_path),
        model="stub",
        notes_to_run={NotesTemplateType.CORP_INFO},
    )
    captured: dict = {}

    async def fake_runner(*, page_hints, **kwargs):
        captured["page_hints"] = page_hints
        # Return a minimal succeeded result — matches the real coordinator's
        # NotesAgentResult shape so the outer task.result() call succeeds.
        from notes.coordinator import NotesAgentResult
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=None,
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_runner):
        await run_notes_extraction(config, infopack=ip)

    assert captured["page_hints"] == [14, 28, 29]


@pytest.mark.asyncio
async def test_coordinator_passes_explicit_hints_unchanged(tmp_path):
    """Caller-supplied config.page_hints wins over infopack derivation."""
    ip = Infopack(
        toc_page=2, page_offset=2,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu", face_page=14,
                note_pages=[28], confidence="HIGH",
            ),
        },
    )
    config = NotesRunConfig(
        pdf_path=str(tmp_path / "x.pdf"),
        output_dir=str(tmp_path),
        model="stub",
        notes_to_run={NotesTemplateType.CORP_INFO},
        page_hints=[99, 100],  # explicit override
    )
    captured: dict = {}

    async def fake_runner(*, page_hints, **kwargs):
        captured["page_hints"] = page_hints
        from notes.coordinator import NotesAgentResult
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=None,
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_runner):
        await run_notes_extraction(config, infopack=ip)

    # Explicit wins — infopack's [14, 28] is ignored.
    assert captured["page_hints"] == [99, 100]


@pytest.mark.asyncio
async def test_coordinator_no_infopack_no_hints(tmp_path):
    """Without an infopack, hints stay empty (CLI-without-scout path)."""
    config = NotesRunConfig(
        pdf_path=str(tmp_path / "x.pdf"),
        output_dir=str(tmp_path),
        model="stub",
        notes_to_run={NotesTemplateType.CORP_INFO},
    )
    captured: dict = {}

    async def fake_runner(*, page_hints, **kwargs):
        captured["page_hints"] = page_hints
        from notes.coordinator import NotesAgentResult
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=None,
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_runner):
        await run_notes_extraction(config, infopack=None)

    assert captured["page_hints"] == []
