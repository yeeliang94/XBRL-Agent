"""Slice 5 — the sub-coordinator must collect per-sub-agent coverage
receipts, attach them to each SubAgentRunResult, expose them on the
aggregate ListOfNotesSubResult, and write them to a
`notes12_coverage.json` side-log.

These tests mock `_invoke_sub_agent_once` to return synthetic (payloads,
coverage_receipt, tokens…) tuples. They do NOT exercise the live agent
loop — receipts come from the mocks as-if the agent had submitted them.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from notes.coverage import CoverageEntry, CoverageReceipt
from notes.listofnotes_subcoordinator import (
    ListOfNotesSubResult,
    SubAgentRunResult,
    run_listofnotes_subcoordinator,
)
from notes.payload import NotesPayload
from scout.notes_discoverer import NoteInventoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _payload(label: str, note_num: int) -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content=f"body for note {note_num}",
        evidence=f"p.{20 + note_num}",
        source_pages=[20 + note_num],
    )


def _inv(note_num: int) -> NoteInventoryEntry:
    return NoteInventoryEntry(
        note_num=note_num,
        title=f"Note {note_num}",
        page_range=(20 + note_num, 20 + note_num),
    )


# ---------------------------------------------------------------------------
# SubAgentRunResult — coverage field
# ---------------------------------------------------------------------------

def test_sub_agent_run_result_carries_optional_coverage_receipt():
    """Field defaults to None so sub-agents that failed before the
    coverage handshake (e.g. 429-exhausted) still serialize cleanly."""
    result = SubAgentRunResult(
        sub_agent_id="sub0",
        batch=[_inv(1)],
        payloads=[],
        status="failed",
    )
    assert hasattr(result, "coverage")
    assert result.coverage is None


def test_sub_agent_run_result_accepts_coverage_receipt():
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=1, action="written", row_labels=["Disclosure of X"]),
    ])
    result = SubAgentRunResult(
        sub_agent_id="sub0",
        batch=[_inv(1)],
        payloads=[],
        status="succeeded",
        coverage=receipt,
    )
    assert result.coverage is receipt


# ---------------------------------------------------------------------------
# Sub-coordinator — side-log + aggregate exposure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subcoordinator_writes_coverage_side_log(tmp_path: Path):
    """The happy path: every sub-agent submits a valid receipt. The
    sub-coordinator collects them and writes notes12_coverage.json with
    one section per sub-agent."""
    inventory = [_inv(i) for i in range(1, 4)]

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
        result = await run_listofnotes_subcoordinator(
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=inventory,
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            parallel=2,
        )

    assert result.coverage_path is not None
    path = Path(result.coverage_path)
    assert path.exists()
    payload = json.loads(path.read_text())
    # Shape: top-level count + entries list, one entry per sub-agent.
    assert "entries" in payload
    assert payload["count"] == len(payload["entries"])
    assert len(payload["entries"]) >= 1
    for entry in payload["entries"]:
        assert "sub_agent_id" in entry
        assert "batch_note_nums" in entry
        assert "receipt" in entry


@pytest.mark.asyncio
async def test_subcoordinator_records_uncovered_notes_for_no_receipt_subs(
    tmp_path: Path,
):
    """If a sub-agent succeeds at writing payloads but never submits a
    receipt (iteration cap hit before the terminal call), the sub-
    coordinator must still record which notes went uncovered. Without
    this, a partial receipt looks identical to a complete one in the
    aggregate."""
    inventory = [_inv(1), _inv(2)]

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        payloads = [_payload("Disclosure of borrowings", e.note_num) for e in batch]
        # Return None in the coverage slot to simulate "agent never
        # called submit_batch_coverage".
        return payloads, 0, 0, None

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_listofnotes_subcoordinator(
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=inventory,
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            parallel=1,
        )

    # Side-log still written, but marks the sub-agent as uncovered.
    assert result.coverage_path is not None
    payload = json.loads(Path(result.coverage_path).read_text())
    entry = payload["entries"][0]
    assert entry["receipt"] is None
    assert entry["uncovered_note_nums"] == [1, 2]


@pytest.mark.asyncio
async def test_subcoordinator_exposes_skipped_reasons_on_result(
    tmp_path: Path,
):
    """Legitimate skips with reasons must flow into the aggregate result
    so the coordinator can convert them into user-visible warnings in
    Slice 6 — otherwise the skip is only visible in the side-log."""
    inventory = [_inv(1), _inv(2)]

    async def fake_invoke(**kwargs):
        batch = kwargs["batch"]
        payloads = [_payload("Disclosure of borrowings", 1)]
        receipt = CoverageReceipt(entries=[
            CoverageEntry(
                note_num=1,
                action="written",
                row_labels=["Disclosure of borrowings"],
            ),
            CoverageEntry(
                note_num=2,
                action="skipped",
                reason="Corporate information — belongs on Sheet 10",
            ),
        ])
        return payloads, 0, 0, receipt

    with patch(
        "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
        side_effect=fake_invoke,
    ):
        result = await run_listofnotes_subcoordinator(
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=inventory,
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            parallel=1,
        )

    # The sub-agent's receipt is attached to its SubAgentRunResult so
    # higher-level code can iterate over skipped entries without re-
    # reading the side-log from disk.
    sub = result.sub_agent_results[0]
    assert sub.coverage is not None
    skipped = [e for e in sub.coverage.entries if e.action == "skipped"]
    assert len(skipped) == 1
    assert "sheet 10" in skipped[0].reason.lower()


@pytest.mark.asyncio
async def test_subcoordinator_no_coverage_path_when_no_sub_agents(
    tmp_path: Path,
):
    """Empty inventory → no sub-agents → no side-log. An empty file
    would misleadingly imply coverage was attempted-and-empty."""
    result = await run_listofnotes_subcoordinator(
        pdf_path=str(tmp_path / "x.pdf"),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        parallel=2,
    )
    assert result.coverage_path is None
    assert not (tmp_path / "notes12_coverage.json").exists()
