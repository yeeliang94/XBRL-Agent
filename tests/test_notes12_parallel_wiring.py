"""Wiring test — ``_run_list_of_notes_fanout`` must pass the model-aware
``parallel`` value from ``pricing.resolve_notes_parallel`` into the
sub-coordinator.

This is the contract that prevents a future change to the coordinator
from silently going back to the hardcoded fan-out width. The resolver
itself is covered by ``test_notes_parallel_resolver.py``; this test only
asserts the plumbing.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from notes.coordinator import _run_list_of_notes_fanout
from notes.listofnotes_subcoordinator import ListOfNotesSubResult
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry


class _FakeModel:
    """Minimal stand-in for a PydanticAI Model instance. Only
    ``model_name`` is read by the resolver + the log line."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


async def _spy_subcoordinator_returning_empty(**kwargs):
    """Spy that records kwargs and returns an empty-but-valid result.

    Returning zero aggregated payloads lands the fanout on the
    "all-sub-agents-failed" branch, which still completes normally and
    records the result — good for a wiring test because we only care
    that ``parallel`` flowed through, not what the writer does after.
    """
    _spy_subcoordinator_returning_empty.kwargs = kwargs  # type: ignore[attr-defined]
    return ListOfNotesSubResult(
        sub_agent_results=[],
        aggregated_payloads=[],
        unmatched_payloads=[],
        unmatched_path=None,
        failures_path=None,
    )


@pytest.mark.asyncio
async def test_cheap_model_fans_out_to_two(tmp_path: Path):
    inventory = [NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i)) for i in range(1, 4)]

    with patch(
        "notes.coordinator.run_listofnotes_subcoordinator",
        side_effect=_spy_subcoordinator_returning_empty,
    ):
        await _run_list_of_notes_fanout(
            pdf_path=str(tmp_path / "dummy.pdf"),
            inventory=inventory,
            filing_level="company",
            model=_FakeModel(model_name="openai.gpt-5.4-mini"),
            output_dir=str(tmp_path),
        )

    # The spy stored the kwargs on the function itself — assert the
    # resolver value reached the sub-coordinator unchanged.
    assert _spy_subcoordinator_returning_empty.kwargs["parallel"] == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_heavy_model_keeps_five_way_fanout(tmp_path: Path):
    inventory = [NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i)) for i in range(1, 4)]

    with patch(
        "notes.coordinator.run_listofnotes_subcoordinator",
        side_effect=_spy_subcoordinator_returning_empty,
    ):
        await _run_list_of_notes_fanout(
            pdf_path=str(tmp_path / "dummy.pdf"),
            inventory=inventory,
            filing_level="company",
            model=_FakeModel(model_name="openai.gpt-5.4"),
            output_dir=str(tmp_path),
        )

    assert _spy_subcoordinator_returning_empty.kwargs["parallel"] == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unknown_model_falls_back_to_default(tmp_path: Path):
    # Unknown models must reach the sub-coordinator with the safe
    # default (5); silent 2-way on an unfamiliar id would halve
    # throughput on heavy models the operator just dropped in.
    from pricing import DEFAULT_NOTES_PARALLEL

    inventory = [NoteInventoryEntry(i, f"Note {i}", (20 + i, 20 + i)) for i in range(1, 4)]

    with patch(
        "notes.coordinator.run_listofnotes_subcoordinator",
        side_effect=_spy_subcoordinator_returning_empty,
    ):
        await _run_list_of_notes_fanout(
            pdf_path=str(tmp_path / "dummy.pdf"),
            inventory=inventory,
            filing_level="company",
            model=_FakeModel(model_name="future-model-not-yet-in-registry"),
            output_dir=str(tmp_path),
        )

    assert _spy_subcoordinator_returning_empty.kwargs["parallel"] == DEFAULT_NOTES_PARALLEL  # type: ignore[attr-defined]
