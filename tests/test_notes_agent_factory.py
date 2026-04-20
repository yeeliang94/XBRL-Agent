"""Unit tests for notes agent factory + prompt rendering."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from notes.agent import NotesDeps, create_notes_agent, render_notes_prompt
from notes_types import NotesTemplateType, notes_template_path
from scout.notes_discoverer import NoteInventoryEntry


@pytest.fixture
def sample_inventory():
    return [
        NoteInventoryEntry(4, "Property, plant and equipment", (20, 22)),
        NoteInventoryEntry(5, "Trade receivables", (23, 24)),
    ]


def test_render_prompt_corporate_info_contains_sheet_and_mode(sample_inventory):
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=sample_inventory,
    )
    # Must mention the sheet name and mode.
    assert "Notes-CI" in prompt
    assert "corporate information" in prompt.lower()
    # Evidence + column rules must be in the base section.
    assert "evidence" in prompt.lower()
    # Inventory preview must include note numbers / titles.
    assert "Property, plant and equipment" in prompt


def test_render_prompt_group_level_includes_group_column_rule():
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="group",
        inventory=[],
    )
    # Group filing prose rule (Section 2 #6) must be stated.
    prompt_lower = prompt.lower()
    assert "group" in prompt_lower
    # Evidence lands in column F for group.
    assert "col f" in prompt_lower or "column f" in prompt_lower


def test_render_prompt_company_level_uses_col_d_for_evidence():
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )
    prompt_lower = prompt.lower()
    assert "col d" in prompt_lower or "column d" in prompt_lower


def _agent_tool_names(agent) -> set[str]:
    """Collect tool names from a PydanticAI agent without touching private
    attributes. Looks at a handful of known-stable locations and falls
    back to iterating toolset entries that expose a `name` attribute.
    """
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None:
            continue
        tools = getattr(ts, "tools", None)
        if tools is None:
            continue
        if isinstance(tools, dict):
            names = {getattr(t, "name", None) or k for k, t in tools.items()}
        else:
            names = {getattr(t, "name", None) for t in tools}
        return {n for n in names if n}
    return set()


def test_create_notes_agent_returns_agent_and_deps(tmp_path: Path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%dummy\n")  # content is never read in factory
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
    )
    assert deps.template_type == NotesTemplateType.CORP_INFO
    assert deps.filing_level == "company"
    assert deps.sheet_name == "Notes-CI"
    assert deps.template_path.endswith("10-Notes-CorporateInfo.xlsx")
    # Agent exposes the expected tools. We avoid pydantic-ai's exact
    # internal layout by probing the handful of known attribute names.
    tool_names = _agent_tool_names(agent)
    assert "view_pdf_pages" in tool_names
    assert "read_template" in tool_names
    assert "write_notes" in tool_names
    assert "save_result" in tool_names


def test_notes_deps_defaults_wrote_once_false(tmp_path: Path):
    """Review I5: wrote_once must start False so the first write overwrites
    any stale filled.xlsx from an earlier run in the same output_dir."""
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%dummy\n")
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
    )
    assert deps.wrote_once is False


def test_notes_deps_filled_filename_uses_template_prefix(tmp_path: Path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%dummy\n")
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="group",
        model="test",
        output_dir=str(tmp_path),
    )
    # Filename convention mirrors extraction agent: NOTES_ISSUED_CAPITAL_filled.xlsx
    assert deps.filled_filename.endswith(".xlsx")
    assert "ISSUED_CAPITAL" in deps.filled_filename
