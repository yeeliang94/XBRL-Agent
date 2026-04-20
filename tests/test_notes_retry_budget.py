"""PLAN §4 Phase E.1 — retry-budget enforcement for single-agent notes sheets.

The coordinator must:
  1. Retry a failed single-agent run exactly once before marking it failed.
  2. Never retry on asyncio.CancelledError (user abort must propagate as-is).
  3. Write a per-sheet ``notes_<TEMPLATE>_failures.json`` side-log when the
     retry budget is exhausted.
  4. Keep sheet-level failures isolated from other sheets (already covered
     by test_coordinator_isolates_per_template_failures).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from notes import coordinator as coord_mod
from notes.coordinator import (
    NotesRunConfig,
    _NoWriteError,
    _run_single_notes_agent,
    _SingleAgentOutcome,
    run_notes_extraction,
)
from notes_types import NotesTemplateType


def _ok(path: str) -> _SingleAgentOutcome:
    """Build a minimal success outcome for tests that don't care about
    writer diagnostics — keeps the test body focused on retry contract."""
    return _SingleAgentOutcome(filled_path=path)


@pytest.mark.asyncio
async def test_single_agent_retries_once_on_exception(tmp_path: Path):
    """An agent that raises on the first attempt must be retried exactly once.

    Using a call-counter on the inner ``_invoke_single_notes_agent_once``
    gives a crisp contract test that survives internal refactors of the
    retry loop.
    """
    calls: list[int] = []

    async def flaky_invoke(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient upstream hiccup")
        # Second attempt succeeds.
        return _ok(str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"))

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=flaky_invoke):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2, "expected exactly one retry (2 total attempts)"
    assert result.status == "succeeded"
    assert result.workbook_path is not None


@pytest.mark.asyncio
async def test_single_agent_stops_at_max_retries(tmp_path: Path):
    """When every attempt fails, the coordinator must give up after
    ``max_retries + 1`` attempts, mark the sheet failed, and persist a
    ``notes_<TEMPLATE>_failures.json`` side-log."""
    calls: list[int] = []

    async def always_fail(**kwargs):
        calls.append(1)
        raise RuntimeError(f"failure #{len(calls)}")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_fail):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.RELATED_PARTY,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2, "PLAN §4 E.1: 1 retry means 2 total attempts"
    assert result.status == "failed"
    assert "failure #2" in (result.error or "")

    # Side-log written with both attempts recorded.
    log_path = tmp_path / "notes_RELATED_PARTY_failures.json"
    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["template"] == "RELATED_PARTY"
    assert len(payload["attempts"]) == 2
    assert payload["attempts"][0]["error"] == "failure #1"
    assert payload["attempts"][1]["error"] == "failure #2"


@pytest.mark.asyncio
async def test_single_agent_retries_silent_no_write(tmp_path: Path):
    """The ``_NoWriteError`` (agent finished without calling write_notes)
    is a retryable condition — the model sometimes succeeds on retry."""
    calls: list[int] = []

    async def no_write_then_succeed(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _NoWriteError("Notes agent finished without writing any payloads")
        return _ok(str(tmp_path / "NOTES_ACC_POLICIES_filled.xlsx"))

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=no_write_then_succeed):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.ACC_POLICIES,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_cancelled_error_never_retries(tmp_path: Path):
    """User abort must propagate as status='cancelled' on the first attempt
    — retrying a cancellation would trap the user's intent to stop."""
    calls: list[int] = []

    async def cancel_now(**kwargs):
        calls.append(1)
        raise asyncio.CancelledError()

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=cancel_now):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.ISSUED_CAPITAL,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 1, "cancellation must NOT be retried"
    assert result.status == "cancelled"
    # No failure side-log — cancellation isn't a failure.
    assert not (tmp_path / "notes_ISSUED_CAPITAL_failures.json").exists()


@pytest.mark.asyncio
async def test_sheet_failure_does_not_block_other_sheets(tmp_path: Path):
    """Cross-sheet isolation — one sheet exhausting its retries must not
    affect a sibling sheet that's running in parallel."""
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.CORP_INFO, NotesTemplateType.ACC_POLICIES},
        filing_level="company",
    )

    # Patch the whole per-agent runner so we skip the retry machinery entirely
    # here; the previous tests already verify the retry contract. This one
    # only checks the outer coordinator's isolation after retries settle.
    async def fake_run(**kwargs):
        from notes.coordinator import NotesAgentResult

        if kwargs["template_type"] == NotesTemplateType.CORP_INFO:
            return NotesAgentResult(
                template_type=NotesTemplateType.CORP_INFO,
                status="failed",
                error="retries exhausted",
            )
        return NotesAgentResult(
            template_type=NotesTemplateType.ACC_POLICIES,
            status="succeeded",
            workbook_path=str(tmp_path / "NOTES_ACC_POLICIES_filled.xlsx"),
        )

    with patch.object(coord_mod, "_run_single_notes_agent", side_effect=fake_run):
        result = await run_notes_extraction(config, infopack=None)

    by_tpl = {r.template_type: r for r in result.agent_results}
    assert by_tpl[NotesTemplateType.CORP_INFO].status == "failed"
    assert by_tpl[NotesTemplateType.ACC_POLICIES].status == "succeeded"
    assert not result.all_succeeded


@pytest.mark.asyncio
async def test_single_agent_surfaces_writer_diagnostics_as_warnings(tmp_path: Path):
    """Peer-review [HIGH]: single-sheet success paths were dropping writer
    skip-errors and borderline fuzzy matches. They now ride through to
    ``NotesAgentResult.warnings`` so history/UI can flag partial successes."""
    async def succeed_with_warnings(**kwargs):
        return _SingleAgentOutcome(
            filled_path=str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"),
            write_errors=[
                "No matching row for label 'Bogus label' in sheet 'Notes-CI'",
            ],
            fuzzy_matches=[
                ("Going concrn", "Going concern", 0.81),   # borderline
                ("Issued capital", "Issued capital", 1.00),  # exact — not a warning
            ],
        )

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=succeed_with_warnings):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert result.status == "succeeded"
    # Writer skip error + borderline fuzzy match surface as distinct warnings.
    assert any("bogus label" in w.lower() for w in result.warnings)
    assert any("borderline fuzzy match" in w.lower() for w in result.warnings)
    # Perfect (score=1.0) matches are NOT warnings — avoid log spam.
    assert not any("1.00" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_max_retries_zero_runs_exactly_once(tmp_path: Path):
    """When max_retries=0 is passed (future override point for ops who want
    faster failures), the coordinator must run exactly once."""
    calls: list[int] = []

    async def always_fail(**kwargs):
        calls.append(1)
        raise RuntimeError("nope")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_fail):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            max_retries=0,
        )

    assert len(calls) == 1
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_cancellation_emit_is_safe_against_queue_teardown(tmp_path: Path):
    """Peer-review #3: a cancelled agent used to await ``_emit`` inside the
    ``except CancelledError`` block. If the surrounding task is being torn
    down hard, that put can itself be cancelled and the terminal return
    never happens. The ``_safe_emit`` wrapper must swallow the inner
    failure so the outer coordinator always gets a cancelled result.
    """
    async def cancel_now(**kwargs):
        raise asyncio.CancelledError()

    # A queue that raises on put — simulates a teardown where the event
    # machinery is already gone by the time the cancellation handler runs.
    class _BrokenQueue:
        async def put(self, _item):
            raise RuntimeError("queue closed during teardown")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=cancel_now):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            event_queue=_BrokenQueue(),
        )

    # Without _safe_emit the broken queue would have bubbled up and the
    # caller would never see a structured cancelled result.
    assert result.status == "cancelled"
