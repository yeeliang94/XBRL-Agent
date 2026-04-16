"""Unit tests for notes/coordinator.py — the notes-extraction fan-out.

The coordinator runs one agent per requested template and collects
`NotesAgentResult`s. These tests mock the per-template agent runner so we
can exercise the orchestration shape without invoking real LLMs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from notes.coordinator import (
    NotesAgentResult,
    NotesCoordinatorResult,
    NotesRunConfig,
    run_notes_extraction,
)
from notes_types import NotesTemplateType
from scout.infopack import Infopack


def _make_config(tmp_path: Path, templates: list[NotesTemplateType]) -> NotesRunConfig:
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    return NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run=set(templates),
        filing_level="company",
    )


@pytest.mark.asyncio
async def test_coordinator_runs_all_requested_templates(tmp_path: Path):
    config = _make_config(
        tmp_path,
        [NotesTemplateType.CORP_INFO, NotesTemplateType.ACC_POLICIES],
    )

    async def fake_run(**kwargs) -> NotesAgentResult:
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=str(tmp_path / f"NOTES_{kwargs['template_type'].value}_filled.xlsx"),
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        result = await run_notes_extraction(config, infopack=None)

    assert isinstance(result, NotesCoordinatorResult)
    assert {r.template_type for r in result.agent_results} == {
        NotesTemplateType.CORP_INFO,
        NotesTemplateType.ACC_POLICIES,
    }
    assert result.all_succeeded
    # workbook_paths keyed by template type for downstream merger
    assert set(result.workbook_paths) == {
        NotesTemplateType.CORP_INFO,
        NotesTemplateType.ACC_POLICIES,
    }


@pytest.mark.asyncio
async def test_coordinator_isolates_per_template_failures(tmp_path: Path):
    config = _make_config(
        tmp_path,
        [NotesTemplateType.CORP_INFO, NotesTemplateType.RELATED_PARTY],
    )

    async def fake_run(**kwargs):
        if kwargs["template_type"] == NotesTemplateType.RELATED_PARTY:
            raise RuntimeError("boom")
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"),
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        result = await run_notes_extraction(config, infopack=None)

    corp = next(r for r in result.agent_results if r.template_type == NotesTemplateType.CORP_INFO)
    rp = next(r for r in result.agent_results if r.template_type == NotesTemplateType.RELATED_PARTY)
    assert corp.status == "succeeded"
    assert rp.status == "failed"
    assert "boom" in (rp.error or "")
    assert not result.all_succeeded


@pytest.mark.asyncio
async def test_coordinator_skips_when_nothing_requested(tmp_path: Path):
    config = NotesRunConfig(
        pdf_path=str(tmp_path / "uploaded.pdf"),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run=set(),
        filing_level="company",
    )

    result = await run_notes_extraction(config, infopack=None)
    assert result.agent_results == []
    assert result.all_succeeded  # vacuous truth — nothing to fail


@pytest.mark.asyncio
async def test_coordinator_passes_inventory_from_infopack(tmp_path: Path):
    from scout.notes_discoverer import NoteInventoryEntry

    config = _make_config(tmp_path, [NotesTemplateType.CORP_INFO])
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[NoteInventoryEntry(1, "Corporate information", (5, 6))],
    )

    received_inventory = {}

    async def fake_run(**kwargs):
        received_inventory["inventory"] = kwargs.get("inventory")
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path="",
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=infopack)

    inv = received_inventory["inventory"]
    assert len(inv) == 1
    assert inv[0].note_num == 1
    assert inv[0].title == "Corporate information"
