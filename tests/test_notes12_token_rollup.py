"""Pinning tests — the Sheet-12 fan-out must roll sub-agent token usage
up onto the returned ``NotesAgentResult``.

Run-168 QA finding: the fan-out summed each sub-agent's tokens but only
wrote the sums to the ``NOTES_LIST_OF_NOTES_cost_report.txt`` file; the
returned result kept its 0 defaults, so the Activity tab showed the
Sheet-12 parent row as "0 tokens · $0.0000" despite real spend.

Per gotcha #6 the PER-TURN detail intentionally stays empty (the
sub-agents merge into one row) — these tests pin that the token ROLLUP
totals populate on every return branch (normal write, all-skipped
carve-out, total failure) while turn_count stays 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from notes.coordinator import _run_list_of_notes_fanout
from notes.coverage import CoverageEntry, CoverageReceipt
from notes.listofnotes_subcoordinator import (
    ListOfNotesSubResult,
    SubAgentRunResult,
)
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry


class _FakeModel:
    """Minimal stand-in — only ``model_name`` is read (resolver, pricing)."""

    def __init__(self, model_name: str = "openai.gpt-5.4") -> None:
        self.model_name = model_name


@dataclass
class _FakeWriteResult:
    """Just the fields the fan-out reads off a successful writer result."""

    success: bool = True
    errors: list = field(default_factory=list)
    rows_written: int = 3
    cells_written: list = field(default_factory=list)
    fuzzy_matches: list = field(default_factory=list)


def _inventory() -> list[NoteInventoryEntry]:
    return [NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i)) for i in (1, 2)]


def _sub_agent(
    sub_id: str,
    batch: list[NoteInventoryEntry],
    *,
    status: str = "succeeded",
    prompt: int = 0,
    completion: int = 0,
    payloads: list | None = None,
    coverage: CoverageReceipt | None = None,
    error: str | None = None,
) -> SubAgentRunResult:
    return SubAgentRunResult(
        sub_agent_id=sub_id,
        batch=batch,
        payloads=payloads or [],
        status=status,
        error=error,
        prompt_tokens=prompt,
        completion_tokens=completion,
        coverage=coverage,
    )


async def _run_fanout_with(
    sub_result: ListOfNotesSubResult, tmp_path: Path, *, write_ok: bool = True,
):
    """Drive the fan-out with a canned sub-coordinator result and a
    mocked workbook writer (the write itself is not under test).

    ``write_ok=False`` simulates a workbook-write failure so the write-failure
    return branch can be exercised."""

    async def _fake_subcoordinator(**kwargs):
        return sub_result

    def _fake_writer(**kwargs):
        return _FakeWriteResult(
            success=write_ok,
            errors=[] if write_ok else ["disk full"],
        )

    with patch(
        "notes.coordinator.run_listofnotes_subcoordinator",
        side_effect=_fake_subcoordinator,
    ), patch(
        # The fan-out imports the writer INSIDE the function body, so the
        # patch must land on the source module, not notes.coordinator.
        "notes.writer.write_notes_workbook",
        side_effect=_fake_writer,
    ):
        return await _run_list_of_notes_fanout(
            pdf_path=str(tmp_path / "dummy.pdf"),
            inventory=_inventory(),
            filing_level="company",
            model=_FakeModel(),
            output_dir=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_success_path_rolls_up_sub_agent_tokens(tmp_path: Path):
    inv = _inventory()
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_agent("sub:1", inv[:1], prompt=1000, completion=200,
                       payloads=[object()]),
            _sub_agent("sub:2", inv[1:], prompt=2000, completion=300,
                       payloads=[object()]),
        ],
        aggregated_payloads=[object(), object()],
    )

    result = await _run_fanout_with(sub_result, tmp_path)

    assert result.status == "succeeded"
    assert result.prompt_tokens == 3000
    assert result.completion_tokens == 500
    assert result.total_tokens == 3500
    assert result.total_cost >= 0.0
    # Gotcha #6: per-turn detail stays empty on the fan-out — only the
    # rollup totals populate.
    assert result.turn_count == 0
    assert result.turns == []


@pytest.mark.asyncio
async def test_all_skipped_carve_out_still_reports_tokens(tmp_path: Path):
    inv = _inventory()
    receipts = [
        CoverageReceipt(entries=[
            CoverageEntry(note_num=e.note_num, action="skipped",
                          reason="belongs on the policies sheet")
        ])
        for e in inv
    ]
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_agent("sub:1", inv[:1], prompt=800, completion=100,
                       coverage=receipts[0]),
            _sub_agent("sub:2", inv[1:], prompt=700, completion=150,
                       coverage=receipts[1]),
        ],
        aggregated_payloads=[],
    )

    result = await _run_fanout_with(sub_result, tmp_path)

    # Deliberately blank sheet is a success — and the sub-agents still
    # burned tokens deciding to skip, so the rollup must show it.
    assert result.status == "succeeded"
    assert result.total_tokens == 1750
    assert result.prompt_tokens == 1500
    assert result.completion_tokens == 250


@pytest.mark.asyncio
async def test_total_failure_still_reports_tokens(tmp_path: Path):
    inv = _inventory()
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_agent("sub:1", inv[:1], status="failed", error="boom",
                       prompt=500, completion=50),
            _sub_agent("sub:2", inv[1:], status="failed", error="boom",
                       prompt=400, completion=60),
        ],
        aggregated_payloads=[],
    )

    result = await _run_fanout_with(sub_result, tmp_path)

    # A failed pass spent real tokens — the row must report them
    # honestly instead of the old 0 defaults.
    assert result.status == "failed"
    assert result.total_tokens == 1010
    assert result.template_type == NotesTemplateType.LIST_OF_NOTES


@pytest.mark.asyncio
async def test_write_failure_still_reports_tokens(tmp_path: Path):
    # Review S-1: a workbook-write failure AFTER the sub-agents ran is its own
    # return branch — it must carry the rollups like the other failure paths,
    # not drop back to the 0 defaults.
    inv = _inventory()
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_agent("sub:1", inv[:1], prompt=1200, completion=300,
                       payloads=[object()]),
            _sub_agent("sub:2", inv[1:], prompt=800, completion=100,
                       payloads=[object()]),
        ],
        aggregated_payloads=[object(), object()],
    )

    result = await _run_fanout_with(sub_result, tmp_path, write_ok=False)

    assert result.status == "failed"
    assert result.error  # carries the writer error
    assert result.total_tokens == 2400
    assert result.prompt_tokens == 2000
    assert result.completion_tokens == 400
