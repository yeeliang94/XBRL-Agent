"""Unit tests for notes/listofnotes_subcoordinator.py (Phase C.1).

Sheet 12 (List of Notes) has 138 target rows. The sub-coordinator splits
scout's inventory across N parallel sub-agents, collects their payloads,
and hands the aggregated list to the writer.

These tests exercise the orchestration shape and the pure helpers (batch
splitting, unmatched-row counting). The per-sub-agent runner is mocked so
no real LLM is invoked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from notes.payload import NotesPayload
from scout.notes_discoverer import NoteInventoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_inventory(n: int, start_page: int = 20) -> list[NoteInventoryEntry]:
    return [
        NoteInventoryEntry(
            note_num=i + 1,
            title=f"Note {i + 1}",
            page_range=(start_page + 2 * i, start_page + 2 * i + 1),
        )
        for i in range(n)
    ]


def _make_payload(label: str, content: str = "body", evidence: str = "page 1") -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content=content,
        evidence=evidence,
        source_pages=[1],
    )


# ---------------------------------------------------------------------------
# Batch splitter
# ---------------------------------------------------------------------------

class TestIterationBudget:
    def test_budget_never_below_global_floor(self):
        # Review I2: small batches still get at least MAX_AGENT_ITERATIONS.
        from agent_tracing import MAX_AGENT_ITERATIONS
        from notes.listofnotes_subcoordinator import _iteration_budget
        assert _iteration_budget(1) >= MAX_AGENT_ITERATIONS
        assert _iteration_budget(3) >= MAX_AGENT_ITERATIONS

    def test_budget_scales_up_for_large_batches(self):
        # A 10-note batch needs enough headroom to not trip the cap on a
        # well-behaved run. 10 notes * 4 iter/note + 10 bootstrap = 50.
        from notes.listofnotes_subcoordinator import _iteration_budget
        assert _iteration_budget(10) >= 50
        # 15-note batch would exceed the global floor; budget must grow.
        assert _iteration_budget(15) > _iteration_budget(5)


class TestSplitInventory:
    def test_splits_into_requested_number_of_batches(self):
        from notes.listofnotes_subcoordinator import split_inventory_contiguous
        inv = _make_inventory(30)
        batches = split_inventory_contiguous(inv, n_batches=5)
        assert len(batches) == 5
        # Round up slicing: 30 / 5 = 6 per batch.
        assert all(len(b) == 6 for b in batches)
        # Flat reassembly must match the original order.
        assert [e.note_num for b in batches for e in b] == [e.note_num for e in inv]

    def test_handles_unequal_split(self):
        from notes.listofnotes_subcoordinator import split_inventory_contiguous
        inv = _make_inventory(7)
        batches = split_inventory_contiguous(inv, n_batches=5)
        # 7 / 5 = 1 rem 2 → two batches of 2, three of 1.
        assert sorted(len(b) for b in batches) == [1, 1, 1, 2, 2]
        # Flat order preserved.
        assert [e.note_num for b in batches for e in b] == [1, 2, 3, 4, 5, 6, 7]

    def test_never_returns_empty_batches(self):
        from notes.listofnotes_subcoordinator import split_inventory_contiguous
        inv = _make_inventory(3)
        batches = split_inventory_contiguous(inv, n_batches=5)
        # Only 3 items — cap batches to 3 non-empty.
        assert len(batches) == 3
        assert all(len(b) >= 1 for b in batches)

    def test_empty_inventory_returns_empty_list(self):
        from notes.listofnotes_subcoordinator import split_inventory_contiguous
        assert split_inventory_contiguous([], n_batches=5) == []

    def test_contiguous_ordering(self):
        from notes.listofnotes_subcoordinator import split_inventory_contiguous
        # Adjacent notes should land in the same batch (page-contiguous rationale).
        inv = _make_inventory(10)
        batches = split_inventory_contiguous(inv, n_batches=5)
        for batch in batches:
            nums = [e.note_num for e in batch]
            assert nums == sorted(nums), f"Batch {batch} not sorted"
            # Within a batch, consecutive note_nums.
            assert all(b - a == 1 for a, b in zip(nums, nums[1:]))


# ---------------------------------------------------------------------------
# Parallel fan-out (mocked sub-agent)
# ---------------------------------------------------------------------------

class TestFanOut:
    @pytest.mark.asyncio
    async def test_runs_one_sub_agent_per_batch(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        inv = _make_inventory(15)
        called_batches: list[list[int]] = []

        async def fake_sub(
            sub_agent_id: str,
            batch: list[NoteInventoryEntry],
            **_: Any,
        ) -> SubAgentRunResult:
            called_batches.append([e.note_num for e in batch])
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id,
                batch=batch,
                payloads=[
                    _make_payload(f"Disclosure of note {e.note_num}") for e in batch
                ],
                status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )
        assert len(result.sub_agent_results) == 5
        assert len(called_batches) == 5
        # Every note ends up in exactly one sub-batch.
        flattened = sorted(n for b in called_batches for n in b)
        assert flattened == list(range(1, 16))
        # Aggregated payloads = flat union.
        assert len(result.aggregated_payloads) == 15

    @pytest.mark.asyncio
    async def test_sub_agent_ids_are_unique(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(5)

        seen_ids: list[str] = []

        async def fake_sub(sub_agent_id, batch, **_):
            seen_ids.append(sub_agent_id)
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id, batch=batch, payloads=[], status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )
        assert len(seen_ids) == 5
        assert len(set(seen_ids)) == 5, f"Expected unique IDs; got {seen_ids}"


# ---------------------------------------------------------------------------
# Retry budget + failure isolation
# ---------------------------------------------------------------------------

class TestRetryAndIsolation:
    @pytest.mark.asyncio
    async def test_one_sub_agent_failure_does_not_block_others(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(10)

        async def fake_sub(sub_agent_id, batch, **_):
            # One specific sub-agent fails; others succeed with payloads.
            if "sub2" in sub_agent_id:
                return SubAgentRunResult(
                    sub_agent_id=sub_agent_id,
                    batch=batch,
                    payloads=[],
                    status="failed",
                    error="simulated failure",
                    retry_count=1,
                )
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id,
                batch=batch,
                payloads=[_make_payload(f"Disclosure of note {e.note_num}") for e in batch],
                status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )
        statuses = [r.status for r in result.sub_agent_results]
        assert statuses.count("failed") == 1
        assert statuses.count("succeeded") == 4
        # Result aggregates payloads from the 4 successful sub-agents only.
        assert len(result.aggregated_payloads) == 8  # 10 items - 2 from failing batch
        assert not result.all_succeeded

    @pytest.mark.asyncio
    async def test_retry_budget_retries_once_then_gives_up(self, tmp_path: Path):
        """When the underlying agent runner raises, the sub-agent wrapper
        retries exactly once, then records a failure."""
        from notes.listofnotes_subcoordinator import (
            _run_list_of_notes_sub_agent,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        batch = _make_inventory(2)

        invocation_count = {"n": 0}

        async def fake_invoke(**_):
            invocation_count["n"] += 1
            raise RuntimeError("model down")

        with patch("notes.listofnotes_subcoordinator._invoke_sub_agent_once",
                   side_effect=fake_invoke):
            result = await _run_list_of_notes_sub_agent(
                sub_agent_id="notes:LIST_OF_NOTES:sub0",
                batch=batch,
                pdf_path=str(pdf_path),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                max_retries=1,
            )
        # Exactly 2 invocations: 1 initial + 1 retry.
        assert invocation_count["n"] == 2
        assert result.status == "failed"
        assert result.retry_count == 1
        assert "model down" in (result.error or "")

    @pytest.mark.asyncio
    async def test_retry_budget_succeeds_on_second_attempt(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            _run_list_of_notes_sub_agent,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        batch = _make_inventory(2)

        attempts = {"n": 0}

        async def fake_invoke(**_):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")
            return [_make_payload("Disclosure of revenue")]

        with patch("notes.listofnotes_subcoordinator._invoke_sub_agent_once",
                   side_effect=fake_invoke):
            result = await _run_list_of_notes_sub_agent(
                sub_agent_id="notes:LIST_OF_NOTES:sub0",
                batch=batch,
                pdf_path=str(pdf_path),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                max_retries=1,
            )
        assert result.status == "succeeded"
        assert result.retry_count == 1
        assert len(result.payloads) == 1


# ---------------------------------------------------------------------------
# Unmatched-row-112 side-logging
# ---------------------------------------------------------------------------

class TestUnmatchedLogging:
    @pytest.mark.asyncio
    async def test_unmatched_payloads_are_tracked_and_written_to_side_log(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(6)

        async def fake_sub(sub_agent_id, batch, **_):
            # Each sub-agent returns one "normal" payload + one "unmatched".
            items = []
            for e in batch:
                items.append(_make_payload(f"Disclosure of revenue"))
                items.append(_make_payload(
                    "Disclosure of other notes to accounts",
                    content=f"Note {e.note_num}: weird",
                    evidence=f"p.{e.page_range[0]}",
                ))
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id, batch=batch, payloads=items, status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )

        # 6 items × 1 "other notes" = 6 unmatched.
        assert len(result.unmatched_payloads) == 6
        # Side-log written.
        assert result.unmatched_path is not None
        side = Path(result.unmatched_path)
        assert side.exists()
        data = json.loads(side.read_text(encoding="utf-8"))
        assert data["count"] == 6
        assert len(data["entries"]) == 6

    @pytest.mark.asyncio
    async def test_no_side_log_written_when_no_unmatched(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(3)

        async def fake_sub(sub_agent_id, batch, **_):
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id,
                batch=batch,
                payloads=[_make_payload("Disclosure of revenue")],
                status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )
        assert result.unmatched_payloads == []
        assert result.unmatched_path is None


# ---------------------------------------------------------------------------
# Failure side-log
# ---------------------------------------------------------------------------

class TestFailureLogging:
    @pytest.mark.asyncio
    async def test_failed_sub_agents_are_written_to_failures_side_log(self, tmp_path: Path):
        from notes.listofnotes_subcoordinator import (
            SubAgentRunResult,
            run_listofnotes_subcoordinator,
        )

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(10)

        async def fake_sub(sub_agent_id, batch, **_):
            if "sub0" in sub_agent_id:
                return SubAgentRunResult(
                    sub_agent_id=sub_agent_id, batch=batch, payloads=[],
                    status="failed", error="bad stuff", retry_count=1,
                )
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id, batch=batch,
                payloads=[_make_payload(f"Disclosure of revenue")], status="succeeded",
            )

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=fake_sub):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=5,
            )
        assert result.failures_path is not None
        data = json.loads(Path(result.failures_path).read_text(encoding="utf-8"))
        assert data["count"] == 1
        assert data["entries"][0]["sub_agent_id"].endswith("sub0")
        assert data["entries"][0]["error"] == "bad stuff"

    @pytest.mark.asyncio
    async def test_unexpected_task_exception_lands_as_failed_sub_result(self, tmp_path: Path):
        """Peer review #2: if the per-sub-agent runner itself raises (bypassing
        its own exception handling), the sub-coordinator must still record
        that batch as a failed SubAgentRunResult — not log-and-drop it."""
        from notes.listofnotes_subcoordinator import run_listofnotes_subcoordinator

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        inv = _make_inventory(4)

        async def explosive_runner(sub_agent_id, batch, **_):
            # Unexpected exception that escapes the retry wrapper entirely.
            raise RuntimeError(f"bug in runner for {sub_agent_id}")

        with patch("notes.listofnotes_subcoordinator._run_list_of_notes_sub_agent",
                   side_effect=explosive_runner):
            result = await run_listofnotes_subcoordinator(
                pdf_path=str(pdf_path),
                inventory=inv,
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                parallel=4,
            )
        # All 4 sub-agents must show up as failed sub-results.
        assert len(result.sub_agent_results) == 4
        assert all(r.status == "failed" for r in result.sub_agent_results)
        assert all("bug in runner" in (r.error or "") for r in result.sub_agent_results)
        # Failure side-log written with all four entries.
        assert result.failures_path is not None
        data = json.loads(Path(result.failures_path).read_text(encoding="utf-8"))
        assert data["count"] == 4


# ---------------------------------------------------------------------------
# Silent-failure regression tests (peer-review #1 + #2)
#
# These pin the two paths where Sheet-12 used to silently report success:
#   (1) empty scout inventory — the fan-out wrapper would ship an untouched
#       template as a green Sheet-12 because the writer treats empty
#       payloads as a no-op success;
#   (2) a non-empty batch yielding zero payloads — no _NoWriteError
#       analogue existed on the sub-agent path, so whole batches could
#       disappear silently.
# Both should fail loudly now; the tests below lock that contract in.
# ---------------------------------------------------------------------------

class TestEmptyInventoryFailsLoudly:
    @pytest.mark.asyncio
    async def test_sheet12_fanout_fails_when_inventory_empty(self, tmp_path: Path):
        """Scout delivers []; Sheet-12 must return status=failed, not silently
        succeed with an untouched template copy.

        Repro path: a scanned PDF where `build_notes_inventory` returns []
        because PyMuPDF can't extract text. Observed in the FINCO run that
        motivated Phase 2."""
        from notes.coordinator import _run_list_of_notes_fanout

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        result = await _run_list_of_notes_fanout(
            pdf_path=str(pdf_path),
            inventory=[],  # <- empty — the silent-failure trigger
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )
        assert result.status == "failed"
        assert result.error is not None
        # Diagnostic must mention inventory so operators can distinguish
        # this from a "sub-agents all failed" failure.
        assert "inventory" in result.error.lower()

    @pytest.mark.asyncio
    async def test_sheet12_fanout_does_not_write_workbook_on_empty_inventory(
        self, tmp_path: Path,
    ):
        """Belt-and-braces: the filled workbook path must not appear when
        we failed before calling the writer. If it did, the merger would
        still pick it up and ship an untouched template copy."""
        from notes.coordinator import _run_list_of_notes_fanout

        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        result = await _run_list_of_notes_fanout(
            pdf_path=str(pdf_path),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )
        # No workbook_path on a failed empty-inventory short-circuit.
        assert result.workbook_path is None
        # And no stray filled.xlsx was written either.
        assert not (tmp_path / "NOTES_LIST_OF_NOTES_filled.xlsx").exists()


class TestZeroPayloadRetryThenFail:
    @pytest.mark.asyncio
    async def test_nonempty_batch_returning_empty_triggers_retry(self, tmp_path: Path):
        """A sub-agent that returns [] for a non-empty batch must retry
        (single-sheet `_NoWriteError` parity). Before the peer-review #2
        fix this silently succeeded with zero payloads."""
        from notes.listofnotes_subcoordinator import (
            _run_list_of_notes_sub_agent,
        )

        call_count = {"n": 0}

        async def always_empty(*, batch, **_: Any):
            call_count["n"] += 1
            return []  # non-empty batch in, empty payloads out — the bug

        batch = _make_inventory(3)
        with patch(
            "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
            side_effect=always_empty,
        ):
            result = await _run_list_of_notes_sub_agent(
                sub_agent_id="sub0",
                batch=batch,
                pdf_path=str(tmp_path / "dummy.pdf"),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                max_retries=1,
            )
        # Original attempt + one retry = 2 invocations.
        assert call_count["n"] == 2
        # Final outcome: failed, not succeeded-with-zero.
        assert result.status == "failed"
        assert result.retry_count == 1
        assert result.payloads == []
        # Error message must make the zero-payload cause obvious — this
        # is what operators will see in the failure side-log.
        assert "payloads" in (result.error or "").lower() or \
               "writing" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_empty_batch_with_empty_payloads_still_succeeds(self, tmp_path: Path):
        """An empty batch is a real empty slice (no notes fell into this
        sub-agent's range) — must pass through as succeeded with zero
        payloads and zero retries, distinct from the zero-payload failure
        case above."""
        from notes.listofnotes_subcoordinator import (
            _run_list_of_notes_sub_agent,
        )

        call_count = {"n": 0}

        async def empty_for_empty_batch(*, batch, **_: Any):
            call_count["n"] += 1
            return []

        with patch(
            "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
            side_effect=empty_for_empty_batch,
        ):
            result = await _run_list_of_notes_sub_agent(
                sub_agent_id="sub0",
                batch=[],  # <- empty batch, not a failure
                pdf_path=str(tmp_path / "dummy.pdf"),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                max_retries=1,
            )
        assert call_count["n"] == 1  # no retry triggered
        assert result.status == "succeeded"
        assert result.payloads == []

    @pytest.mark.asyncio
    async def test_nonempty_batch_empty_first_payloads_second_succeeds(self, tmp_path: Path):
        """First attempt returns [] (retry trigger), second attempt
        returns payloads — the sub-agent recovers and is marked succeeded
        with the recovered payloads. Proves the retry actually runs and
        its output is used, not thrown away."""
        from notes.listofnotes_subcoordinator import (
            _run_list_of_notes_sub_agent,
        )

        attempts: list[int] = []
        good = [_make_payload("Label 1", "body")]

        async def first_empty_then_ok(*, batch, **kwargs: Any):
            attempts.append(kwargs.get("attempt", -1))
            return [] if len(attempts) == 1 else list(good)

        batch = _make_inventory(2)
        with patch(
            "notes.listofnotes_subcoordinator._invoke_sub_agent_once",
            side_effect=first_empty_then_ok,
        ):
            result = await _run_list_of_notes_sub_agent(
                sub_agent_id="sub0",
                batch=batch,
                pdf_path=str(tmp_path / "dummy.pdf"),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                max_retries=1,
            )
        assert attempts == [0, 1]  # first attempt + one retry
        assert result.status == "succeeded"
        assert result.payloads == good
        assert result.retry_count == 1
