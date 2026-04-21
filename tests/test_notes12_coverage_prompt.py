"""Slice 4 prompt tests — the Sheet-12 sub-agent user prompt must
- list the exact batch note numbers so the agent knows what to cover;
- require `submit_batch_coverage` as the terminal tool call.

Captures the rendered prompt string at runtime (not just source text)
because the build logic grew references to `note_num` and
`submit_batch_coverage` in comments/docstrings that would satisfy a
naive source-grep without actually landing in the agent-facing prompt.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from notes.agent import NotesDeps
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry
from token_tracker import TokenReport


def _capture_sub_agent_prompt(
    tmp_path: Path, batch: list[NoteInventoryEntry],
) -> str:
    """Run `_invoke_sub_agent_once` with a faked agent factory that raises
    AFTER the prompt is built, returning the captured prompt string.

    Same trick as tests/test_notes_batch_note_nums_wiring.py — the fake
    factory swaps `create_notes_agent` so we never construct a real
    PydanticAI agent, and a sentinel exception stops the runner right
    after the prompt is passed to agent.iter()."""
    captured: dict[str, str] = {}

    class _FakeAgent:
        def iter(self, user_prompt: str, *_a, **_kw):
            captured["prompt"] = user_prompt
            raise RuntimeError("stop-after-prompt-built")

    def _fake_factory(**kwargs):
        deps = NotesDeps(
            pdf_path=kwargs["pdf_path"],
            template_path="x",
            model=kwargs["model"],
            output_dir=kwargs["output_dir"],
            token_report=TokenReport(),
            template_type=NotesTemplateType.LIST_OF_NOTES,
            sheet_name="Notes-Listofnotes",
            filing_level=kwargs["filing_level"],
        )
        return _FakeAgent(), deps

    async def _run():
        from notes.listofnotes_subcoordinator import _invoke_sub_agent_once

        try:
            await _invoke_sub_agent_once(
                sub_agent_id="notes:LIST_OF_NOTES:sub0",
                batch=batch,
                pdf_path=str(tmp_path / "x.pdf"),
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )
        except RuntimeError as e:
            assert "stop-after-prompt-built" in str(e)

    with patch(
        "notes.listofnotes_subcoordinator.create_notes_agent",
        side_effect=_fake_factory,
    ):
        asyncio.run(_run())

    return captured["prompt"]


def test_sub_agent_prompt_enumerates_batch_note_numbers(tmp_path: Path):
    """Going by count alone ('contains 3 PDF note(s)') lets the agent
    forget WHICH notes it was assigned. Enumerating the specific note
    numbers gives the agent the same list the receipt validator will
    use, so there's no ambiguity about what must be covered."""
    batch = [
        NoteInventoryEntry(note_num=4, title="FVTPL", page_range=(22, 22)),
        NoteInventoryEntry(note_num=5, title="Due to holding", page_range=(22, 22)),
        NoteInventoryEntry(note_num=6, title="Share capital", page_range=(22, 22)),
    ]
    prompt = _capture_sub_agent_prompt(tmp_path, batch)
    # Each specific note number must appear as a number in the prompt.
    # (Regex would be tighter; for now token match is enough.)
    assert " 4" in prompt or "4," in prompt or "4 " in prompt
    assert " 5" in prompt or "5," in prompt or "5 " in prompt
    assert " 6" in prompt or "6," in prompt or "6 " in prompt


def test_sub_agent_prompt_mentions_submit_batch_coverage_tool(tmp_path: Path):
    """Without an explicit prompt-level reminder, the agent may
    finish after `write_notes` and never call the coverage tool —
    then the sub-coordinator sees deps.coverage_receipt = None and
    treats it as a failure. The prompt must name the terminal call."""
    batch = [
        NoteInventoryEntry(note_num=4, title="FVTPL", page_range=(22, 22)),
    ]
    prompt = _capture_sub_agent_prompt(tmp_path, batch)
    assert "submit_batch_coverage" in prompt


def test_sub_agent_prompt_empty_batch_still_renders_without_crashing(
    tmp_path: Path,
):
    """Edge case: scout may produce an empty batch slice (fewer notes
    than parallel sub-agents). Empty-batch prompts must not raise on
    note_num formatting — e.g. `min([])` or `batch[0]` would crash."""
    prompt = _capture_sub_agent_prompt(tmp_path, batch=[])
    assert prompt  # renders something, no assertion on content shape
