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


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
@pytest.mark.parametrize("level", ["company", "group"])
def test_related_party_prompt_requires_categories_without_empty_fallback(standard, level):
    """Guard the live failure: valid amounts submitted without a column category."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.RELATED_PARTY,
        filing_level=level, filing_standard=standard, inventory=[],
    )
    flat = " ".join(prompt.split())
    assert "Every payload containing `numeric_values` must also contain non-empty" in flat
    assert "transactions AND outstanding balances, including zero values" in flat
    assert "corporate-information note and any referenced balance note" in flat
    assert "fellow subsidiary under common control uses Other related parties" in flat
    assert "Emit separate payloads" in flat
    assert "Do not use Other related parties or Total simply to fill a missing category" in flat
    assert "Do not submit an unclassified numeric payload" in flat
    assert "before write_notes and check every numeric payload before save_result" in flat
    assert "retain its label and evidence with empty dimensions" not in flat


def test_related_party_category_requirement_does_not_change_capital_fallback():
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        filing_level="company", inventory=[],
    )
    assert "retain its label and evidence with empty dimensions" in prompt
    assert "Every related-party numeric payload" not in prompt


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
    assert "calculator" in tool_names


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


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["full", "top-half"])
async def test_missing_policy_report_accepts_pages_inspected_by_zoom(tmp_path, monkeypatch, region):
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    from db import repository as repo
    from db.schema import init_db
    from notes import agent as na, reviewer_agent as ra

    label = "*Disclosure of material accounting policy information"
    db = tmp_path / "run.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "source.pdf", config={"notes_to_run": ["ACC_POLICIES"]})
        conn.execute("INSERT INTO notes_nodes(node_uuid,template_id,sheet,row,label,kind,slot_role) "
                     "VALUES ('required','mfrs-company-notes-policies-v1',?,4,?,'LEAF','INPUT')",
                     (ra.POLICIES_SHEET, label))
    agent, deps = create_notes_agent(NotesTemplateType.ACC_POLICIES, "unused.pdf", [], "company",
        TestModel(), output_dir=str(tmp_path), filing_standard="mfrs", db_path=str(db), run_id=run_id)
    funcs = {name: tool.function for ts in agent.toolsets for name, tool in getattr(ts, "tools", {}).items()}
    ctx = SimpleNamespace(deps=deps)
    monkeypatch.setattr(na, "count_pdf_pages", lambda path: 2)
    async def render(path, page, dpi, *, policy, clip=None):
        return b"png"
    monkeypatch.setattr(na, "_render_one_page_single_flight", render)
    args = dict(row=4, target_label=label, source_pages=[2], reason="Only specific policies are present.",
                evidence="Page 2 opens directly with the revenue policy.")
    await funcs["zoom_pdf_region"](ctx, 1, region)
    assert "rejected" in funcs["report_missing_mandatory_field"](ctx, **args)  # page 2 not inspected
    await funcs["zoom_pdf_region"](ctx, 2, region)
    assert "Recorded" in funcs["report_missing_mandatory_field"](ctx, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize("standard,level,label", [
    ("mfrs", "company", "*Disclosure of material accounting policy information"),
    ("mpers", "group", "Disclosure of significant accounting policies"),
])
async def test_missing_policy_report_is_grounded_persisted_and_given_to_review(tmp_path, monkeypatch, standard, level, label):
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    from db import repository as repo
    from db.schema import init_db
    from notes import agent as na, reviewer_agent as ra

    db = tmp_path / "run.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "source.pdf", config={"notes_to_run": ["ACC_POLICIES"]})
        conn.execute("INSERT INTO notes_nodes(node_uuid,template_id,sheet,row,label,kind,slot_role) "
                     "VALUES ('required',?,?,4,?,'LEAF','INPUT')",
                     (f"{standard}-{level}-notes-policies-v1", ra.POLICIES_SHEET, label))
    agent, deps = create_notes_agent(NotesTemplateType.ACC_POLICIES, "unused.pdf", [], level,
        TestModel(), output_dir=str(tmp_path), filing_standard=standard, db_path=str(db), run_id=run_id)
    funcs = {name: tool.function for ts in agent.toolsets for name, tool in getattr(ts, "tools", {}).items()}
    ctx = SimpleNamespace(deps=deps)
    report = funcs["report_missing_mandatory_field"]
    args = dict(row=4, target_label=label, source_pages=[1], reason="Only heading and specific policies are present.",
                evidence="The policies start with a heading followed by dedicated subsections.")
    assert "rejected" in report(ctx, **args)
    monkeypatch.setattr(na, "count_pdf_pages", lambda path: 1)
    async def render(path, pages):
        return {p: b"png" for p in pages}
    monkeypatch.setattr(na, "_render_pages_async", render)
    await funcs["view_pdf_pages"](ctx, [1])
    assert "rejected" in report(ctx, **{**args, "target_label": "Wrong label"})
    assert "Recorded" in report(ctx, **args)
    assert "Recorded" in report(ctx, **args)
    await funcs["save_result"](ctx)
    with repo.db_session(db) as conn:
        flags = repo.fetch_notes_review_flags(conn, run_id)
        assert len(flags) == 1 and flags[0]["reason"] == args["reason"]
        assert flags[0]["source_pages"] == [1] and flags[0]["evidence"] == args["evidence"]
        assert repo.list_notes_cells_for_run(conn, run_id) == []
    _, _, context = ra.create_notes_reviewer_agent(run_id=run_id, db_path=str(db), pdf_path="unused.pdf",
        filing_standard=standard, filing_level=level, model=TestModel(), output_dir=str(tmp_path))
    assert context["mandatory_fields"][0]["extraction_disposition"]["reason"] == args["reason"]
    packet = ra.build_notes_reviewer_packet(context)
    assert args["evidence"] in packet and ra.count_open_items(context) > 0
    with repo.db_session(db) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=ra.POLICIES_SHEET, row=4, label=label, html="<p>Policy overview.</p>")
    assert "rejected" in report(ctx, **args)


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

@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
@pytest.mark.parametrize("level", ["company", "group"])
@pytest.mark.parametrize("prepared", [False, True])
def test_effective_notes_prompts_preserve_required_routing(standard, level, prepared):
    policy = render_notes_prompt(
        NotesTemplateType.ACC_POLICIES, level, [], filing_standard=standard,
        prepared_source_required=prepared,
    )
    flat = " ".join(policy.split())
    assert "not just a title" in flat or "not a title alone" in flat
    assert "Basis of Preparation" in policy
    assert f"Filing standard: {standard.upper()}" in policy
    assert "active policies catalog" in policy
    assert "source introduction" in flat or "source-supported introductory" in flat
    listing = render_notes_prompt(
        NotesTemplateType.LIST_OF_NOTES, level, [], filing_standard=standard,
        prepared_source_required=prepared,
    )
    flat = " ".join(listing.split())
    assert "disclosure of trade and other payables" in flat.lower()
    assert "Do not repeat" in flat
    assert "related-party dual placement" not in flat
