"""Tests for the `submit_batch_coverage` tool on Sheet-12 sub-agents.

Contract:
- The tool is only registered when `NotesDeps.batch_note_nums` is set
  (Sheet-12 sub-agent mode). Sheets 10/11/13/14 don't expose it.
- Parses the receipt JSON from the agent, validates it against
  (batch_note_nums, payload_sink row labels), and either stashes the
  receipt on `deps.coverage_receipt` or returns an error string for
  the agent to retry against.

Tested via a module-level helper so we don't need a live RunContext —
same pattern as `_sub_agent_sink_write` in Slice 0b.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import NotesDeps, _submit_coverage_impl
from notes.coverage import CoverageReceipt
from notes.payload import NotesPayload
from notes_types import NotesTemplateType
from token_tracker import TokenReport


def _deps_in_sub_agent_mode(
    tmp_path: Path,
    batch_note_nums: list[int],
    sink_labels: list[str],
) -> NotesDeps:
    """Shape a NotesDeps the way `_invoke_sub_agent_once` would for
    Sheet-12 — batch_note_nums populated, sink populated, receipt empty."""
    deps = NotesDeps(
        pdf_path=str(tmp_path / "fake.pdf"),
        template_path=str(tmp_path / "fake.xlsx"),
        model="test",
        output_dir=str(tmp_path),
        token_report=TokenReport(),
        template_type=NotesTemplateType.LIST_OF_NOTES,
        sheet_name="Notes-Listofnotes",
        filing_level="company",
    )
    deps.payload_sink = [
        NotesPayload(
            chosen_row_label=label,
            content="body",
            evidence="p. 22",
            source_pages=[22],
        )
        for label in sink_labels
    ]
    deps.batch_note_nums = list(batch_note_nums)
    deps.sub_agent_id = "notes:LIST_OF_NOTES:sub0"
    return deps


# ---------------------------------------------------------------------------
# Happy path: valid receipt gets stashed, no error
# ---------------------------------------------------------------------------

def test_valid_receipt_stashes_on_deps_and_returns_accepted(tmp_path: Path):
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4, 5, 6],
        sink_labels=[
            "Disclosure of financial instruments at fair value through profit or loss",
            "Disclosure of trade and other payables",
            "Disclosure of share capital",
        ],
    )
    raw = """
    [
      {"note_num": 4, "action": "written",
       "row_labels": ["Disclosure of financial instruments at fair value through profit or loss"]},
      {"note_num": 5, "action": "written",
       "row_labels": ["Disclosure of trade and other payables"]},
      {"note_num": 6, "action": "written",
       "row_labels": ["Disclosure of share capital"]}
    ]
    """
    result = _submit_coverage_impl(deps, raw)
    assert "accepted" in result.lower()
    assert isinstance(deps.coverage_receipt, CoverageReceipt)
    assert len(deps.coverage_receipt.entries) == 3


def test_valid_receipt_with_skip_is_accepted(tmp_path: Path):
    """Skip is a legitimate outcome (cross-sheet note, no fitting row).
    The tool must accept it and record the reason — upstream formatters
    turn this into a user-visible warning."""
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[1, 2],
        sink_labels=["Disclosure of financial instruments"],
    )
    raw = """
    [
      {"note_num": 1, "action": "skipped",
       "reason": "Corporate information — belongs on Sheet 10"},
      {"note_num": 2, "action": "written",
       "row_labels": ["Disclosure of financial instruments"]}
    ]
    """
    result = _submit_coverage_impl(deps, raw)
    assert "accepted" in result.lower()
    assert deps.coverage_receipt is not None
    skipped = [e for e in deps.coverage_receipt.entries if e.action == "skipped"]
    assert len(skipped) == 1
    assert "sheet 10" in skipped[0].reason.lower()


# ---------------------------------------------------------------------------
# Rejection paths — each surfaces structured error text the agent reads,
# and leaves deps.coverage_receipt unset so the caller knows to retry.
# ---------------------------------------------------------------------------

def test_missing_batch_note_rejected(tmp_path: Path):
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4, 5, 6],
        sink_labels=["Disclosure of share capital"],
    )
    raw = """
    [
      {"note_num": 6, "action": "written",
       "row_labels": ["Disclosure of share capital"]}
    ]
    """
    result = _submit_coverage_impl(deps, raw)
    assert "missing" in result.lower() or "uncovered" in result.lower()
    # Explicit note numbers in the error so the agent knows exactly what
    # to add on its retry.
    assert "4" in result and "5" in result
    assert deps.coverage_receipt is None, (
        "Invalid receipts must NOT be stashed — coordinator reads the "
        "absence as 'sub-agent did not complete the handshake'."
    )


def test_extra_note_rejected(tmp_path: Path):
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4],
        sink_labels=["Disclosure of share capital"],
    )
    raw = """
    [
      {"note_num": 4, "action": "written",
       "row_labels": ["Disclosure of share capital"]},
      {"note_num": 99, "action": "skipped",
       "reason": "thought it was in my batch"}
    ]
    """
    result = _submit_coverage_impl(deps, raw)
    assert "99" in result
    assert deps.coverage_receipt is None


def test_written_entry_with_unknown_row_label_rejected(tmp_path: Path):
    """The agent can't claim 'written to row X' if no payload with label
    X is in its sink — that's an inconsistent receipt and the tool must
    force the agent to reconcile."""
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4],
        sink_labels=["Disclosure of share capital"],
    )
    raw = """
    [
      {"note_num": 4, "action": "written",
       "row_labels": ["Disclosure of something I never wrote"]}
    ]
    """
    result = _submit_coverage_impl(deps, raw)
    lower = result.lower()
    assert "something i never wrote" in lower
    assert deps.coverage_receipt is None


def test_malformed_json_rejected(tmp_path: Path):
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4],
        sink_labels=["Disclosure of share capital"],
    )
    result = _submit_coverage_impl(deps, "not a json list")
    assert "json" in result.lower() or "parse" in result.lower()
    assert deps.coverage_receipt is None


def test_envelope_dict_rejected_must_be_list(tmp_path: Path):
    """Keep the wire format tight — a dict envelope drifts naming
    conventions and bloats tool-call tokens for no benefit."""
    deps = _deps_in_sub_agent_mode(
        tmp_path,
        batch_note_nums=[4],
        sink_labels=["Disclosure of share capital"],
    )
    result = _submit_coverage_impl(deps, '{"entries": []}')
    assert "list" in result.lower()
    assert deps.coverage_receipt is None


# ---------------------------------------------------------------------------
# Guards — tool must not be invoked in the wrong mode
# ---------------------------------------------------------------------------

def test_submit_coverage_requires_batch_note_nums(tmp_path: Path):
    """A NotesDeps without batch_note_nums came from a non-sub-agent
    template (Sheet 10/11/13/14). The tool should have been filtered
    out at agent-construction time, but belt-and-braces: calling it
    anyway returns a clear configuration error rather than crashing."""
    deps = NotesDeps(
        pdf_path=str(tmp_path / "fake.pdf"),
        template_path=str(tmp_path / "fake.xlsx"),
        model="test",
        output_dir=str(tmp_path),
        token_report=TokenReport(),
        template_type=NotesTemplateType.CORP_INFO,
        sheet_name="Notes-CI",
        filing_level="company",
    )
    # No batch_note_nums set — this deps is not in sub-agent mode.
    result = _submit_coverage_impl(deps, "[]")
    assert (
        "not available" in result.lower()
        or "not in sub-agent mode" in result.lower()
        or "batch_note_nums" in result.lower()
    )
    assert deps.coverage_receipt is None


# ---------------------------------------------------------------------------
# Tool registration — only on sub-agent mode
# ---------------------------------------------------------------------------

def _agent_tool_names(agent) -> set[str]:
    """Same pattern as test_notes_agent_factory._agent_tool_names."""
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


def test_submit_batch_coverage_tool_registered_on_sub_agent_mode(tmp_path: Path):
    """When the deps has batch_note_nums, the tool must be available so
    the agent can call it as its terminal step."""
    from notes.agent import create_notes_agent

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        batch_note_nums=[4, 5, 6],
    )
    names = _agent_tool_names(agent)
    assert "submit_batch_coverage" in names


def test_submit_batch_coverage_tool_absent_from_single_sheet_agents(
    tmp_path: Path,
):
    """Non-Sheet-12 templates have no batch to cover — exposing the
    tool there would confuse the agent into fabricating a receipt."""
    from notes.agent import create_notes_agent

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    agent, _ = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
    )
    names = _agent_tool_names(agent)
    assert "submit_batch_coverage" not in names
