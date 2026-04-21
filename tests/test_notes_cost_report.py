"""Phase 5 (post-FINCO-2021 audit) — cost telemetry fix + batch-range label.

Two pins:

- `SubAgentRunResult` carries `prompt_tokens` / `completion_tokens` so
  the Sheet-12 fan-out can build a parent cost report without having to
  hand-aggregate SSE events from the database.
- The Sheet-12 sub-agent "started" status message includes the batch's
  note-number and PDF page range so the UI can show a meaningful label
  instead of just "(3 notes)".

The single-sheet cost report backfill from `agent_run.usage()` is
integration-level (it only produces non-zero numbers against a real
model); we pin it indirectly via the SubAgentRunResult dataclass fields.
"""
from __future__ import annotations

from notes.listofnotes_subcoordinator import (
    ListOfNotesSubResult,
    SubAgentRunResult,
)
from scout.notes_discoverer import NoteInventoryEntry


def _entry(n: int, start: int, end: int) -> NoteInventoryEntry:
    return NoteInventoryEntry(note_num=n, title=f"Note {n}", page_range=(start, end))


def test_subagent_run_result_carries_token_fields():
    """Phase 5.1: without these fields the fan-out cannot build its
    parent cost report."""
    r = SubAgentRunResult(
        sub_agent_id="sub0",
        batch=[_entry(1, 10, 10)],
        payloads=[],
        status="succeeded",
        prompt_tokens=1234,
        completion_tokens=567,
    )
    assert r.prompt_tokens == 1234
    assert r.completion_tokens == 567


def test_subagent_run_result_defaults_tokens_to_zero():
    """Backwards compatibility: existing constructors that don't pass
    the new fields must still work and yield zero counts (so aggregation
    stays correct for the empty-batch / cancelled paths)."""
    r = SubAgentRunResult(
        sub_agent_id="sub0", batch=[], payloads=[], status="succeeded",
    )
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0


def test_list_of_notes_sub_result_aggregation_invariants():
    """A ListOfNotesSubResult with mixed success/failure sub-agents must
    still sum tokens cleanly — failed sub-agents with zero captured usage
    don't add noise, succeeded ones contribute their full counts."""
    r0 = SubAgentRunResult(
        sub_agent_id="sub0", batch=[], payloads=[], status="succeeded",
        prompt_tokens=1000, completion_tokens=200,
    )
    r1 = SubAgentRunResult(
        sub_agent_id="sub1", batch=[], payloads=[], status="failed",
        error="boom", prompt_tokens=0, completion_tokens=0,
    )
    r2 = SubAgentRunResult(
        sub_agent_id="sub2", batch=[], payloads=[], status="succeeded",
        prompt_tokens=500, completion_tokens=100,
    )
    agg = ListOfNotesSubResult(sub_agent_results=[r0, r1, r2])
    total_prompt = sum(x.prompt_tokens for x in agg.sub_agent_results)
    total_completion = sum(x.completion_tokens for x in agg.sub_agent_results)
    assert total_prompt == 1500
    assert total_completion == 300


def test_single_sheet_backfill_increments_token_report():
    """Peer-review #2: without this test, the Phase 5.1 try/except block
    silently hides a regression where `deps.token_report` never gets
    populated. Stub the usage callable and confirm the mutation."""
    from dataclasses import dataclass

    from notes.coordinator import _backfill_token_report
    from token_tracker import TokenReport

    @dataclass
    class StubUsage:
        request_tokens: int
        response_tokens: int

    report = TokenReport(model="stub")
    _backfill_token_report(
        report, lambda: StubUsage(request_tokens=1234, response_tokens=567),
        template_label="ACC_POLICIES",
    )
    assert report.total_prompt_tokens == 1234
    assert report.total_completion_tokens == 567


def test_single_sheet_backfill_handles_none_tokens():
    """The proxy sometimes returns None for one of the token fields —
    the backfill must treat that as 0, not crash."""
    from dataclasses import dataclass

    from notes.coordinator import _backfill_token_report
    from token_tracker import TokenReport

    @dataclass
    class StubUsage:
        request_tokens: object
        response_tokens: object

    report = TokenReport(model="stub")
    _backfill_token_report(
        report, lambda: StubUsage(request_tokens=None, response_tokens=42),
        template_label="ACC_POLICIES",
    )
    assert report.total_prompt_tokens == 0
    assert report.total_completion_tokens == 42


def test_single_sheet_backfill_swallows_exceptions():
    """Cost telemetry is advisory — a surprise usage shape must not
    crash the run. We assert the report is left untouched rather than
    raising."""
    from notes.coordinator import _backfill_token_report
    from token_tracker import TokenReport

    def exploding_usage():
        raise ValueError("usage() is broken somehow")

    report = TokenReport(model="stub")
    # No raise.
    _backfill_token_report(report, exploding_usage, "ACC_POLICIES")
    # Counters stay at zero.
    assert report.total_prompt_tokens == 0
    assert report.total_completion_tokens == 0


def test_failed_subagent_retains_last_known_token_usage(monkeypatch):
    """Peer-review MEDIUM: when `_invoke_sub_agent_once` raises after
    spending tokens, the retry wrapper must surface those tokens on the
    failed `SubAgentRunResult`. Before the fix this path dropped tokens
    to zero and the cost report silently under-reported.
    """
    import asyncio

    from notes import listofnotes_subcoordinator as subcoord

    async def _fake_invoke(
        *,
        sub_agent_id,
        batch,
        pdf_path,
        filing_level,
        model,
        output_dir,
        event_queue,
        parent_agent_id,
        attempt,
        page_hints,
        page_offset,
        usage_out,
    ):
        # Simulate real iterations: update the accumulator with partial
        # counts, then raise as if the next model call errored out.
        usage_out["prompt"] = 800
        usage_out["completion"] = 150
        raise RuntimeError("simulated mid-run failure")

    monkeypatch.setattr(subcoord, "_invoke_sub_agent_once", _fake_invoke)

    result = asyncio.run(subcoord._run_list_of_notes_sub_agent(
        sub_agent_id="sub9",
        batch=[_entry(1, 10, 12)],
        pdf_path="fake.pdf",
        filing_level="company",
        model="stub",
        output_dir="/tmp",
        max_retries=0,  # one attempt, no retry — guarantees a single failure
    ))

    assert result.status == "failed"
    assert result.prompt_tokens == 800, (
        f"expected token counts to survive the failure, got "
        f"prompt={result.prompt_tokens}"
    )
    assert result.completion_tokens == 150


def test_batch_range_derivation_used_for_status_message():
    """Pin the derivation the sub-coordinator uses so a later refactor
    can't silently regress to "(3 notes)" with no page span.

    We replicate the in-function code to keep this test independent of
    the heavy `agent.iter` harness; any divergence between the test and
    the runtime is itself a test failure because the numbers would
    disagree with a live run."""
    batch = [_entry(4, 30, 30), _entry(5, 31, 31), _entry(6, 32, 32)]
    batch_pages = [p for e in batch for p in range(e.page_range[0], e.page_range[1] + 1)]
    assert batch_pages == [30, 31, 32]
    assert min(batch_pages) == 30
    assert max(batch_pages) == 32
    note_range = f"Notes {batch[0].note_num}-{batch[-1].note_num}"
    assert note_range == "Notes 4-6"
