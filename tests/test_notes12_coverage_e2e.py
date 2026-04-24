"""End-to-end coverage-receipt flow through the real coordinator.

Slices 1-6 covered the units and the pairwise integrations. This file
exercises the whole Sheet-12 pipeline — coordinator → sub-coordinator →
sub-agent runner — with mocked LLM interactions, asserting the coverage
receipt lands in the NotesAgentResult warnings and in the side-log
file the UI/history reads.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from notes.coordinator import NotesRunConfig, run_notes_extraction
from notes.coverage import CoverageEntry, CoverageReceipt
from notes.payload import NotesPayload
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry
from scout.infopack import Infopack


def _payload(label: str, note_num: int) -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content=f"body for note {note_num}",
        evidence=f"p.{20 + note_num}",
        source_pages=[20 + note_num],
        parent_note={"number": "1", "title": "Test Note"},
    )


@pytest.mark.asyncio
async def test_e2e_clean_receipt_no_coverage_warnings(tmp_path: Path):
    """Happy path: sub-agents submit valid receipts covering every
    batch note. No coverage warnings should appear in the agent result,
    and the notes12_coverage.json side-log is written."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i))
            for i in range(1, 4)
        ],
    )

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        payloads = [_payload("Disclosure of borrowings", e.note_num) for e in batch]
        receipt = CoverageReceipt(entries=[
            CoverageEntry(
                note_num=e.note_num,
                action="written",
                row_labels=["Disclosure of borrowings"],
            )
            for e in batch
        ])
        return payloads, 0, 0, receipt

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    assert agent.status == "succeeded", agent.error
    # No coverage-related warning lines on a fully-covered run.
    joined = " | ".join(agent.warnings)
    assert "skipped" not in joined
    assert "uncovered" not in joined.lower()

    # Side-log exists and has one entry per sub-agent, each with a
    # populated receipt and no uncovered notes.
    coverage_path = tmp_path / "notes12_coverage.json"
    assert coverage_path.exists()
    payload = json.loads(coverage_path.read_text())
    for entry in payload["entries"]:
        assert entry["receipt"] is not None
        assert entry["uncovered_note_nums"] == []


@pytest.mark.asyncio
async def test_e2e_skip_with_reason_surfaces_warning(tmp_path: Path):
    """A legitimate skip with a reason flows all the way up to
    NotesAgentResult.warnings as a 'Note N skipped: <reason>' line."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(1, "Corporate information", (16, 16)),
            NoteInventoryEntry(2, "Cash and short-term funds", (21, 21)),
        ],
    )

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        # Pretend the agent wrote note 2 to a cash row, and skipped
        # note 1 because it belongs on Sheet 10.
        payloads = [
            _payload("Disclosure of cash and cash equivalents", e.note_num)
            for e in batch
            if e.note_num == 2
        ]
        receipt_entries: list[CoverageEntry] = []
        for e in batch:
            if e.note_num == 1:
                receipt_entries.append(CoverageEntry(
                    note_num=1,
                    action="skipped",
                    reason="Corporate information — belongs on Sheet 10",
                ))
            else:
                receipt_entries.append(CoverageEntry(
                    note_num=e.note_num,
                    action="written",
                    row_labels=["Disclosure of cash and cash equivalents"],
                ))
        return payloads, 0, 0, CoverageReceipt(entries=receipt_entries)

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    assert agent.status == "succeeded", agent.error
    joined = " | ".join(agent.warnings)
    assert "Note 1 skipped" in joined
    assert "Sheet 10" in joined


@pytest.mark.asyncio
async def test_e2e_fully_skipped_batch_is_success_not_failure(tmp_path: Path):
    """Peer-review [HIGH]: when every sub-agent submits a valid receipt
    that skips every batch note, the sheet MUST succeed (with warnings)
    not fail. Pre-fix, the fanout-layer guard treated empty
    aggregated_payloads as total failure — only the sub-agent-layer
    carve-out had been relaxed."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(1, "Corporate information", (16, 16)),
            NoteInventoryEntry(2, "Accounting policies", (17, 17)),
            NoteInventoryEntry(3, "Related party transactions", (24, 24)),
        ],
    )

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        # Every note in this batch belongs on a different sheet.
        receipt = CoverageReceipt(entries=[
            CoverageEntry(
                note_num=e.note_num,
                action="skipped",
                reason=f"Note {e.note_num} belongs on a different sheet",
            )
            for e in batch
        ])
        return [], 0, 0, receipt

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    # Not failed — every sub-agent submitted a valid receipt and
    # legitimately chose to skip everything.
    assert agent.status == "succeeded", (
        f"expected succeeded, got {agent.status}: {agent.error}"
    )
    # Every skip shows up as a warning — the operator still sees WHY
    # the sheet is blank.
    joined = " | ".join(agent.warnings)
    assert "Note 1 skipped" in joined
    assert "Note 2 skipped" in joined
    assert "Note 3 skipped" in joined


@pytest.mark.asyncio
async def test_e2e_fully_skipped_batch_emits_workbook_for_merge(tmp_path: Path):
    """Peer-review [HIGH]: a requested Sheet-12 whose sub-agents legitimately
    skipped every note must still produce a workbook file so the merger
    doesn't silently drop Notes-Listofnotes from the final ``filled.xlsx``.

    Before the fix the skip-only success path returned
    ``workbook_path=None`` and wrote nothing to disk. A user who ticked
    "List of Notes" then received a merged workbook without a
    Notes-Listofnotes sheet and no signal anything went wrong.
    """
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(1, "Corporate information", (16, 16)),
            NoteInventoryEntry(2, "Accounting policies", (17, 17)),
        ],
    )

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        receipt = CoverageReceipt(entries=[
            CoverageEntry(
                note_num=e.note_num,
                action="skipped",
                reason="belongs elsewhere",
            )
            for e in batch
        ])
        return [], 0, 0, receipt

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    assert agent.status == "succeeded", agent.error
    assert agent.workbook_path is not None, (
        "skip-only success must still emit a workbook_path so the merger "
        "can include Notes-Listofnotes in the final filled.xlsx"
    )
    assert Path(agent.workbook_path).exists(), (
        f"workbook_path {agent.workbook_path} was set but the file is missing"
    )

    # Sanity check: the written file contains the Notes-Listofnotes sheet
    # (i.e. it's a real template copy, not a zero-byte stub).
    import openpyxl  # local import — matches other tests in this file
    wb = openpyxl.load_workbook(agent.workbook_path)
    try:
        assert "Notes-Listofnotes" in wb.sheetnames
    finally:
        wb.close()


@pytest.mark.asyncio
async def test_e2e_empty_aggregate_without_receipt_still_fails(tmp_path: Path):
    """The carve-out is narrow: empty aggregate + NO receipt is still
    a failure. Without this the sheet would silently report succeeded
    on an untouched workbook — exactly the 'silent success' failure
    mode the coverage machinery was built to prevent."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(i, f"N{i}", (20 + i, 20 + i))
            for i in range(1, 4)
        ],
    )

    async def fake_invoke(**kwargs):
        return [], 0, 0, None  # empty + no receipt — should fail

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    assert agent.status == "failed"


@pytest.mark.asyncio
async def test_e2e_missing_receipt_surfaces_uncovered_warnings(tmp_path: Path):
    """A sub-agent that succeeds at writing but never submits a
    receipt (iteration-cap before terminal call) has its batch notes
    surfaced as uncovered warnings — one line per note_num."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.LIST_OF_NOTES},
        filing_level="company",
    )
    infopack = Infopack(
        toc_page=1,
        page_offset=0,
        notes_inventory=[
            NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i))
            for i in range(3, 6)
        ],
    )

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        # Payloads present, no receipt.
        payloads = [_payload("Disclosure of borrowings", e.note_num) for e in batch]
        return payloads, 0, 0, None

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_notes_extraction(config, infopack=infopack)

    agent = result.agent_results[0]
    joined = " | ".join(agent.warnings)
    # Each batch note shows up as uncovered.
    assert "Note 3" in joined
    assert "Note 4" in joined
    assert "Note 5" in joined
    assert "uncovered" in joined.lower()
