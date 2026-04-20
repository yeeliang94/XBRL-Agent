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
async def test_coordinator_routes_per_template_model_override(tmp_path: Path):
    """NotesRunConfig.models takes precedence over NotesRunConfig.model on a
    per-template basis. Templates without an override fall back to .model.
    """
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="default-model",
        notes_to_run={NotesTemplateType.CORP_INFO, NotesTemplateType.ACC_POLICIES},
        filing_level="company",
        models={NotesTemplateType.ACC_POLICIES: "claude-override"},
    )

    # Capture the `model` kwarg each task receives so we can assert the
    # coordinator resolves per-template correctly.
    received_models: dict[NotesTemplateType, str] = {}

    async def fake_run(**kwargs):
        received_models[kwargs["template_type"]] = kwargs["model"]
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=str(tmp_path / f"NOTES_{kwargs['template_type'].value}.xlsx"),
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=None)

    assert received_models[NotesTemplateType.ACC_POLICIES] == "claude-override"
    assert received_models[NotesTemplateType.CORP_INFO] == "default-model"


def test_notes_run_config_model_for_helper(tmp_path: Path):
    """Sanity check for the resolver — keeps the fallback contract documented."""
    config = NotesRunConfig(
        pdf_path=str(tmp_path / "x.pdf"),
        output_dir=str(tmp_path),
        model="fallback",
        models={NotesTemplateType.RELATED_PARTY: "override"},
    )
    assert config.model_for(NotesTemplateType.RELATED_PARTY) == "override"
    assert config.model_for(NotesTemplateType.CORP_INFO) == "fallback"


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


# ---------------------------------------------------------------------------
# Single-agent success gate (peer-review #1): an agent that finishes without
# ever invoking write_notes must report status="failed", not a silent success
# with workbook_path=None. Otherwise merge/download downstream try to consume
# a non-existent file and History flags the run as green.
# ---------------------------------------------------------------------------


class _FakeNode:
    """Stand-in node that Agent.is_call_tools_node / is_model_request_node
    both reject, so _run_single_notes_agent's per-iteration branches are
    no-ops and the loop completes without any tool calls."""


class _FakeAgentRun:
    def __init__(self):
        self.result = object()

    def usage(self):
        class U:
            total_tokens = 0
            request_tokens = 0
            response_tokens = 0
        return U()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def __aiter__(self):
        async def _gen():
            # One iteration, no tool calls — mirrors an agent that stopped
            # without ever touching write_notes.
            if False:
                yield  # pragma: no cover
        return _gen()


class _FakeAgent:
    def iter(self, _prompt, deps=None):
        return _FakeAgentRun()


@pytest.mark.asyncio
async def test_single_notes_agent_without_writes_reports_failed(tmp_path: Path):
    """Agent completes its reasoning loop but never calls write_notes —
    deps.wrote_once stays False, deps.filled_path stays empty. The coordinator
    must detect the no-op and return status='failed' so a downstream merger
    doesn't try to ingest a non-existent workbook."""
    from notes import coordinator as coord_mod
    from notes.agent import NotesDeps, TokenReport

    def fake_create_notes_agent(**kwargs):
        deps = NotesDeps(
            pdf_path=kwargs["pdf_path"],
            template_path="/tmp/fake-template.xlsx",
            model=kwargs["model"],
            output_dir=kwargs["output_dir"],
            token_report=TokenReport(),
            template_type=kwargs["template_type"],
            sheet_name=kwargs["template_type"].value,
            filing_level=kwargs["filing_level"],
            inventory=kwargs.get("inventory") or [],
            filled_filename=f"NOTES_{kwargs['template_type'].value}_filled.xlsx",
        )
        return _FakeAgent(), deps

    with patch.object(coord_mod, "create_notes_agent", side_effect=fake_create_notes_agent), \
         patch.object(coord_mod, "save_agent_trace"):
        result = await coord_mod._run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert result.status == "failed"
    assert result.workbook_path is None
    assert "without writing" in (result.error or "")
