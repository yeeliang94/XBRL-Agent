"""Slice 2 wiring — `batch_note_nums` must flow from the sub-coordinator
into `NotesDeps` so the `submit_batch_coverage` tool (Slice 3) can check
the coverage receipt against the actual batch.

Two invariants:
- `NotesDeps` carries a `batch_note_nums` field (and a sibling
  `coverage_receipt` for the Slice-3 handshake), both defaulting to
  None so Sheets 10/11/13/14 (single-agent, non-batched) keep their
  existing shape.
- `_invoke_sub_agent_once` populates `batch_note_nums` alongside
  `payload_sink` and `sub_agent_id`. We inspect the function source for
  the assignment because constructing the full agent.iter harness to
  introspect live deps is way heavier than the contract warrants.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from notes.agent import NotesDeps, create_notes_agent
from notes_types import NotesTemplateType


def test_notes_deps_exposes_batch_note_nums_field(tmp_path: Path):
    """Field default must be None so non-batched templates (10/11/13/14)
    keep their existing behaviour — the `submit_batch_coverage` tool is
    only registered when this field is populated."""
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
    )
    assert hasattr(deps, "batch_note_nums")
    assert deps.batch_note_nums is None


def test_notes_deps_exposes_coverage_receipt_field(tmp_path: Path):
    """Sibling field for the Slice-3 handshake. Starts None; the tool
    overwrites it after a valid receipt is submitted."""
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
    )
    assert hasattr(deps, "coverage_receipt")
    assert deps.coverage_receipt is None


def test_notes_deps_batch_note_nums_mutable_list_default_is_safe():
    """List-valued dataclass fields often mis-share a mutable default
    across instances. Default must be None (not a shared list) so the
    sub-coordinator only opts in explicitly."""
    a = NotesDeps(
        pdf_path="x",
        template_path="x",
        model="x",
        output_dir="x",
        token_report=None,  # type: ignore[arg-type]
        template_type=NotesTemplateType.CORP_INFO,
        sheet_name="Notes-CI",
        filing_level="company",
    )
    b = NotesDeps(
        pdf_path="y",
        template_path="y",
        model="y",
        output_dir="y",
        token_report=None,  # type: ignore[arg-type]
        template_type=NotesTemplateType.CORP_INFO,
        sheet_name="Notes-CI",
        filing_level="company",
    )
    # Setting the field on one instance must not affect the other.
    a.batch_note_nums = [1, 2, 3]
    assert b.batch_note_nums is None


def test_invoke_sub_agent_once_assigns_batch_note_nums_to_deps():
    """The sub-coordinator is the only entry point that knows what
    note numbers a batch contains. Source-level pin that it writes them
    onto deps — if someone refactors the deps setup and forgets this
    line, the coverage receipt can't validate against the batch and the
    whole Slice-3 retry loop degrades to a no-op."""
    from notes.listofnotes_subcoordinator import _invoke_sub_agent_once

    src = inspect.getsource(_invoke_sub_agent_once)
    assert "batch_note_nums" in src, (
        "_invoke_sub_agent_once must set deps.batch_note_nums so the "
        "submit_batch_coverage tool (registered on that condition) can "
        "validate the receipt against the real batch."
    )
    # Belt-and-braces: the assignment uses the entries' note_num attr.
    assert "note_num" in src


def test_invoke_sub_agent_once_populates_from_batch_entries(tmp_path: Path):
    """Behavioural check via monkey-patching. We fake `create_notes_agent`
    so it returns a tuple we can inspect after the sub-coordinator sets
    its fields — skips the live PydanticAI agent construction entirely.
    """
    import asyncio
    from unittest.mock import patch

    from notes.agent import NotesDeps
    from notes_types import NotesTemplateType as NT
    from scout.notes_discoverer import NoteInventoryEntry
    from token_tracker import TokenReport

    captured: dict[str, NotesDeps] = {}

    class _FakeAgent:
        """Minimal agent stand-in — the sub-coordinator only needs
        something with an `.iter()` context manager for the happy path
        we're testing. Here we raise after the deps are set up so the
        test stays tight and doesn't need a full agent.iter simulator."""

        def iter(self, *_a, **_kw):
            raise RuntimeError("stop-after-deps-setup")

    def _fake_factory(**kwargs):
        deps = NotesDeps(
            pdf_path=kwargs["pdf_path"],
            template_path="x",
            model=kwargs["model"],
            output_dir=kwargs["output_dir"],
            token_report=TokenReport(),
            template_type=NT.LIST_OF_NOTES,
            sheet_name="Notes-Listofnotes",
            filing_level=kwargs["filing_level"],
        )
        captured["deps"] = deps
        return _FakeAgent(), deps

    batch = [
        NoteInventoryEntry(note_num=4, title="Financial asset", page_range=(22, 22)),
        NoteInventoryEntry(note_num=5, title="Amount due holding", page_range=(22, 22)),
        NoteInventoryEntry(note_num=6, title="Share capital", page_range=(22, 22)),
    ]

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
            # Expected — the fake agent aborts after deps are set so we
            # don't need to simulate a full agent.iter round-trip.
            assert "stop-after-deps-setup" in str(e)

    with patch(
        "notes.listofnotes_subcoordinator.create_notes_agent",
        side_effect=_fake_factory,
    ):
        asyncio.run(_run())

    assert "deps" in captured, "factory should have been invoked"
    assert captured["deps"].batch_note_nums == [4, 5, 6]


def test_invoke_sub_agent_once_registers_submit_batch_coverage_tool(
    tmp_path: Path,
):
    """Integration pin for peer-review [HIGH]: the tool registration
    check in create_notes_agent fires at FACTORY time against the
    incoming batch_note_nums kwarg. If the subcoordinator sets the
    field post-factory (as an earlier commit did) the tool never
    registers — a bug the unit tests in test_notes_submit_coverage.py
    could NOT catch because they pass batch_note_nums= to the factory
    directly.

    This test goes through the live _invoke_sub_agent_once path with a
    REAL agent constructed. A sentinel exception on agent.iter() stops
    the run right after the tool registration phase, so we never need
    a live model.
    """
    import asyncio

    from notes_types import NotesTemplateType as NT
    from scout.notes_discoverer import NoteInventoryEntry

    captured: dict[str, object] = {}

    # Save the real factory so we can call it after capturing kwargs.
    from notes.agent import create_notes_agent as _real_factory

    def _wrap_factory(**kwargs):
        """Thin wrapper that calls the real factory, stashes the agent,
        and swaps in an agent.iter() that raises the sentinel.

        We can't use Mock().patch_object on the agent because it's a
        PydanticAI `Agent` and we need the real class's tool list. So
        we construct normally, then monkeypatch only the iter method."""
        agent, deps = _real_factory(**kwargs)
        captured["agent"] = agent
        captured["deps"] = deps
        captured["factory_kwargs"] = kwargs

        def _stop(*_a, **_kw):
            raise RuntimeError("stop-after-construction")

        # PydanticAI Agent.iter is an instance method; monkeypatch it.
        import types
        agent.iter = types.MethodType(  # type: ignore[attr-defined]
            lambda self, *a, **kw: _stop(), agent,
        )
        return agent, deps

    batch = [
        NoteInventoryEntry(note_num=4, title="FVTPL", page_range=(22, 22)),
        NoteInventoryEntry(note_num=5, title="Due to holding", page_range=(22, 22)),
    ]

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
            assert "stop-after-construction" in str(e)

    from unittest.mock import patch

    with patch(
        "notes.listofnotes_subcoordinator.create_notes_agent",
        side_effect=_wrap_factory,
    ):
        asyncio.run(_run())

    # The subcoordinator MUST pass batch_note_nums into the factory —
    # the tool registration depends on it being present at factory
    # time, not on post-hoc deps mutation.
    factory_kwargs = captured["factory_kwargs"]
    assert factory_kwargs.get("batch_note_nums") == [4, 5], (
        "_invoke_sub_agent_once must pass batch_note_nums= into "
        "create_notes_agent(). The previous post-factory assignment "
        "arrives too late for the tool registration check."
    )

    # And the live agent actually has submit_batch_coverage registered.
    agent = captured["agent"]
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None:
            continue
        tools = getattr(ts, "tools", None)
        if tools is None:
            continue
        names = (
            set(tools.keys()) if isinstance(tools, dict)
            else {getattr(t, "name", None) for t in tools}
        )
        break
    else:
        raise AssertionError("Could not introspect agent toolset")
    assert "submit_batch_coverage" in names, (
        f"submit_batch_coverage not registered on the live agent; "
        f"tools={sorted(n for n in names if n)}"
    )
