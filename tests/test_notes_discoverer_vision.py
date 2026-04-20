"""Unit tests for scout.notes_discoverer_vision.

Covers the pure helpers (_chunk, _merge_and_stitch), the vision-agent
factory, _scan_batch (with a mocked model), and the _vision_inventory
orchestrator's failure-aggregation behaviour.

The integration / live test against the FINCO PDF lives in
test_scout_notes_inventory_vision_live.py (marked @pytest.mark.live).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from scout.notes_discoverer import NoteInventoryEntry
from scout.notes_discoverer_vision import (
    BATCH_SIZE,
    OVERLAP,
    VisionBatchError,
    _build_vision_agent,
    _chunk,
    _merge_and_stitch,
    _scan_batch,
    _vision_inventory,
    _VisionBatch,
    _VisionNote,
)


# ---------------------------------------------------------------------------
# Step 1.3 — _chunk
# ---------------------------------------------------------------------------


class TestChunk:
    def test_basic_overlap(self):
        """Plan Step 1.3 Verify case 1: 10..20 with size=8, overlap=1.

        Two batches, the last page of batch 1 reappears as the first of
        batch 2 so a header sitting on page 17 is visible to both.
        """
        assert _chunk(10, 20, 8, 1) == [
            [10, 11, 12, 13, 14, 15, 16, 17],
            [17, 18, 19, 20],
        ]

    def test_single_page(self):
        """Plan Step 1.3 Verify case 2: single-page range."""
        assert _chunk(10, 10, 8, 1) == [[10]]

    def test_fifty_pages_contiguous_with_overlap(self):
        """Plan Step 1.3 Verify case 3: 50-page notes section.

        With size=8 and stride=(size-overlap)=7 we expect ⌈49/7⌉+1 = 8 batches,
        each overlap page appears at the boundary, no page beyond 60
        leaks out.
        """
        batches = _chunk(10, 60, 8, 1)
        # Contiguous coverage — every page 10..60 appears in at least one batch.
        covered = {p for b in batches for p in b}
        assert covered == set(range(10, 61))
        # Last page of each (non-terminal) batch appears first in the next.
        for i in range(len(batches) - 1):
            assert batches[i][-1] == batches[i + 1][0]
        # Terminal batch must end at 60 exactly.
        assert batches[-1][-1] == 60
        # No batch exceeds BATCH_SIZE — keeps token budget predictable.
        assert all(len(b) <= 8 for b in batches)

    def test_rejects_zero_size(self):
        with pytest.raises(ValueError):
            _chunk(1, 5, 0, 0)

    def test_rejects_overlap_ge_size(self):
        with pytest.raises(ValueError):
            _chunk(1, 5, 3, 3)

    def test_rejects_end_before_start(self):
        with pytest.raises(ValueError):
            _chunk(10, 5, 4, 1)


# ---------------------------------------------------------------------------
# Step 2.1 — _merge_and_stitch
# ---------------------------------------------------------------------------


def _note(num: int, title: str, first: int, last: int) -> _VisionNote:
    return _VisionNote(note_num=num, title=title, first_page=first, last_page=last)


class TestMergeAndStitch:
    def test_overlap_dedup_takes_widest_range(self):
        """Plan Step 2.1 Verify case 1: two batches each report note 4.

        Batch A sees header on page 10 through page 13; batch B, overlapping
        on page 13, sees it continue through page 15. Dedup to a single
        entry that spans the union — 10..15. As the only (terminal) note
        the stitcher now trusts the LLM's widened last_page=15 (post-
        MEDIUM fix), not `notes_end=20`.
        """
        a = _VisionBatch(entries=[_note(4, "PPE", 10, 13)])
        b = _VisionBatch(entries=[_note(4, "PPE", 13, 15)])
        out = _merge_and_stitch([a, b], notes_end=20)
        assert out == [NoteInventoryEntry(note_num=4, title="PPE", page_range=(10, 15))]

    def test_trailing_edge_derived_from_next_note(self):
        """Plan Step 2.1 Verify case 2: three notes (4, 5, 7).

        Note 4 ends at note 5's first_page - 1 = 13; note 5 ends at
        note 7's first_page - 1 = 19; note 7 is terminal, so its last
        page comes from the LLM (25) clamped to notes_end=30 → stays 25.
        Post-MEDIUM fix: the terminal note no longer stretches to
        notes_end when the LLM reported a tighter value.
        """
        batch = _VisionBatch(entries=[
            _note(4, "PPE",      10, 12),
            _note(5, "Receivables", 14, 14),
            _note(7, "Taxation",   20, 25),
        ])
        out = _merge_and_stitch([batch], notes_end=30)
        assert [(e.note_num, e.page_range) for e in out] == [
            (4, (10, 13)),
            (5, (14, 19)),
            (7, (20, 25)),
        ]

    def test_terminal_note_clamps_to_notes_end(self):
        """MEDIUM-peer-review safety check: if the LLM hallucinates a
        last_page past notes_end, the stitcher must clamp down to
        notes_end rather than trust the bogus value.
        """
        batch = _VisionBatch(entries=[
            _note(4, "Final",   10, 40),  # LLM said it ends on page 40…
        ])
        out = _merge_and_stitch([batch], notes_end=25)  # …but we know notes end at 25
        assert out == [NoteInventoryEntry(note_num=4, title="Final", page_range=(10, 25))]

    def test_terminal_note_does_not_absorb_post_notes_pages(self):
        """MEDIUM-peer-review regression: the terminal note used to be
        stretched to `notes_end = pdf_length`, silently absorbing
        Directors' Statement / auditor's report pages when the caller
        didn't know the true notes-section end. With the fix in place,
        absent an explicit `notes_end_page` from the caller, the
        terminal note still respects the LLM's last_page.
        """
        batch = _VisionBatch(entries=[
            _note(14, "Capital mgmt", 30, 31),  # LLM: note ends on page 31
        ])
        # Caller passes pdf_length=37 (the pre-fix default when
        # notes_end_page is unknown); pages 32..37 are trailing non-note
        # sections. The final note must not swallow them.
        out = _merge_and_stitch([batch], notes_end=37)
        assert out == [NoteInventoryEntry(note_num=14, title="Capital mgmt", page_range=(30, 31))]

    def test_out_of_order_input_is_sorted(self):
        """Plan Step 2.1 Verify case 3: out-of-order batch input."""
        batch = _VisionBatch(entries=[
            _note(7, "Tax",      20, 25),
            _note(4, "PPE",      10, 12),
        ])
        out = _merge_and_stitch([batch], notes_end=30)
        assert [e.note_num for e in out] == [4, 7]

    def test_drops_malformed_entries_but_keeps_others(self):
        """Plan Step 2.1 Verify case 4: one bogus entry should not kill the rest.

        An entry where first_page > notes_end is impossible — drop it.
        The surviving entry must still be returned.
        """
        batch = _VisionBatch(entries=[
            _note(4, "PPE",      10, 12),
            _note(99, "Garbage", 500, 510),
        ])
        out = _merge_and_stitch([batch], notes_end=30)
        assert [e.note_num for e in out] == [4]

    def test_first_note_not_at_pagerange_start(self):
        """Edge: a gap before the first note shouldn't invent entries.

        The sole (and therefore terminal) note's last_page comes from
        the LLM (20), clamped to notes_end=25 → stays 20. Pre-MEDIUM
        fix this stretched to 25.
        """
        batch = _VisionBatch(entries=[_note(3, "Leases", 15, 20)])
        out = _merge_and_stitch([batch], notes_end=25)
        assert out == [NoteInventoryEntry(note_num=3, title="Leases", page_range=(15, 20))]


# ---------------------------------------------------------------------------
# Step 2.2 — _build_vision_agent
# ---------------------------------------------------------------------------


class TestBuildVisionAgent:
    def test_returns_agent_with_batch_output_type(self):
        """Plan Step 2.2 Verify: agent constructs cleanly on a TestModel."""
        from pydantic_ai.models.test import TestModel

        agent = _build_vision_agent(TestModel())
        # The class may be an Agent subclass depending on pydantic-ai version,
        # so just assert the instance has a .run coroutine method.
        assert hasattr(agent, "run")
        assert callable(agent.run)


# ---------------------------------------------------------------------------
# Step 2.3 — _scan_batch (retry behaviour)
# ---------------------------------------------------------------------------


_FAKE_PDF_PATH = "/tmp/fake.pdf"


class _StubUsage:
    """Minimal AgentRunResult-ish usage object for tests."""
    def __init__(self, input_tokens: int = 100, output_tokens: int = 20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _StubResult:
    """Minimal AgentRunResult stand-in exposing both .output and .usage()."""
    def __init__(self, output: _VisionBatch):
        self.output = output

    def usage(self) -> _StubUsage:
        return _StubUsage()


class _StubAgent:
    """Async-compatible agent stub for _scan_batch tests.

    `run_side_effects` is a list consumed in order. Each entry is either
    a `_VisionBatch` (returned) or an exception type/instance (raised).
    """
    def __init__(self, run_side_effects):
        self._effects = list(run_side_effects)
        self.calls = 0

    async def run(self, _prompt):
        self.calls += 1
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return _StubResult(effect)


@pytest.fixture
def _patch_render(monkeypatch):
    """Stub render_pages_to_png_bytes so _scan_batch doesn't touch the disk."""
    def _fake_render(path, start, end, dpi):
        # Return one dummy PNG byte-string per page.
        return [b"\x89PNG-fake"] * (end - start + 1)
    monkeypatch.setattr(
        "scout.notes_discoverer_vision.render_pages_to_png_bytes",
        _fake_render,
    )


class TestScanBatch:
    def test_returns_valid_batch_on_first_call(self, _patch_render):
        batch = _VisionBatch(entries=[_note(4, "PPE", 10, 12)])
        agent = _StubAgent([batch])
        # _scan_batch now returns the full run result (so orchestrator can
        # read .usage()). The parsed batch is under .output.
        result = asyncio.run(_scan_batch(_FAKE_PDF_PATH, agent, [10, 11, 12]))
        assert result.output.entries[0].note_num == 4
        assert agent.calls == 1

    def test_retries_once_on_failure(self, _patch_render):
        """Plan Step 2.3 Verify case 2: one retry on malformed output."""
        batch = _VisionBatch(entries=[_note(4, "PPE", 10, 12)])
        agent = _StubAgent([ValueError("bad json"), batch])
        result = asyncio.run(_scan_batch(_FAKE_PDF_PATH, agent, [10, 11, 12]))
        assert result.output.entries[0].note_num == 4
        assert agent.calls == 2

    def test_raises_after_second_failure(self, _patch_render):
        """Plan Step 2.3 Verify case 3: two failures in a row → VisionBatchError."""
        agent = _StubAgent([ValueError("bad 1"), ValueError("bad 2")])
        with pytest.raises(VisionBatchError):
            asyncio.run(_scan_batch(_FAKE_PDF_PATH, agent, [10, 11, 12]))
        assert agent.calls == 2


# ---------------------------------------------------------------------------
# Step 2.4 — _vision_inventory orchestrator
# ---------------------------------------------------------------------------


class TestVisionInventory:
    """Orchestrator tests. We patch _scan_batch rather than _build_vision_agent
    because _scan_batch is the natural seam — it's what we stub in CI to avoid
    real LLM calls."""

    def test_happy_path_stitches_batches(self, monkeypatch):
        """Two batches → merged + stitched inventory."""
        # _vision_inventory no longer opens a shared fitz.Document — it
        # just calls _build_vision_agent + _scan_batch, both of which we
        # stub below. No fitz patching needed after the fix-2 refactor.
        monkeypatch.setattr(
            "scout.notes_discoverer_vision._build_vision_agent",
            lambda _m: object(),  # never used; _scan_batch is stubbed
        )

        # _scan_batch returns AgentRunResult (we use _StubResult with .output + .usage()).
        canned = iter([
            _StubResult(_VisionBatch(entries=[_note(1, "CorpInfo", 10, 12), _note(2, "SigAcc", 13, 15)])),
            _StubResult(_VisionBatch(entries=[_note(2, "SigAcc", 15, 17), _note(3, "Leases", 18, 19)])),
        ])

        async def fake_scan(_pdf_path, _agent, _pages):
            return next(canned)

        monkeypatch.setattr("scout.notes_discoverer_vision._scan_batch", fake_scan)

        out = asyncio.run(_vision_inventory(
            "/tmp/fake.pdf", start=10, end=20,
            model=object(),  # unused — _build_vision_agent is stubbed
        ))
        # Three notes, stitched trailing edges. Note 3 is terminal →
        # post-MEDIUM-fix it uses min(LLM-last_page=19, notes_end=20)=19
        # instead of stretching to notes_end.
        assert [(e.note_num, e.page_range) for e in out] == [
            (1, (10, 12)),
            (2, (13, 17)),
            (3, (18, 19)),
        ]

    def test_one_batch_failure_leaves_others_intact(self, monkeypatch):
        monkeypatch.setattr(
            "scout.notes_discoverer_vision._build_vision_agent",
            lambda _m: object(),
        )

        canned = iter([
            _StubResult(_VisionBatch(entries=[_note(1, "Corp", 10, 12)])),
            VisionBatchError("boom"),
        ])

        async def fake_scan(_pdf_path, _agent, _pages):
            nxt = next(canned)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        monkeypatch.setattr("scout.notes_discoverer_vision._scan_batch", fake_scan)

        out = asyncio.run(_vision_inventory(
            "/tmp/fake.pdf", start=10, end=20, model=object(),
        ))
        # Batch-1's note survives even though batch-2 failed.
        assert [e.note_num for e in out] == [1]

    def test_all_batches_fail_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "scout.notes_discoverer_vision._build_vision_agent",
            lambda _m: object(),
        )

        async def always_fail(_pdf_path, _agent, _pages):
            raise VisionBatchError("nope")

        monkeypatch.setattr("scout.notes_discoverer_vision._scan_batch", always_fail)

        out = asyncio.run(_vision_inventory(
            "/tmp/fake.pdf", start=10, end=20, model=object(),
        ))
        assert out == []

    def test_logs_token_usage_on_success(self, monkeypatch, caplog):
        """Plan Step 5.1 Verify: cost-visibility log line fires."""
        monkeypatch.setattr(
            "scout.notes_discoverer_vision._build_vision_agent",
            lambda _m: object(),
        )

        canned = iter([
            _StubResult(_VisionBatch(entries=[_note(1, "Corp", 10, 12)])),
        ])

        async def fake_scan(_pdf_path, _agent, _pages):
            return next(canned)

        monkeypatch.setattr("scout.notes_discoverer_vision._scan_batch", fake_scan)

        with caplog.at_level("INFO", logger="scout.notes_discoverer_vision"):
            asyncio.run(_vision_inventory(
                "/tmp/fake.pdf", start=10, end=12, model=object(),
            ))

        # The stub usage() returns input_tokens=100, output_tokens=20 per batch
        # (see _StubUsage). With one batch we expect "input=100 output=20".
        assert any(
            "vision inventory tokens" in r.message
            and "input=100" in r.message
            and "output=20" in r.message
            for r in caplog.records
        ), f"no token log found. records: {[r.message for r in caplog.records]}"
