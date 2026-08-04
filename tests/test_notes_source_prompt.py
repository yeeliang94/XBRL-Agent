"""Pin the Word-source formatting block + tool gating (PLAN-word-input Step 9).

The block and the read_source_note tool must appear ONLY when a source.html
sidecar exists for the run — PDF-only runs render exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import (
    _render_source_html_block,
    create_notes_agent,
    render_notes_prompt,
)
from notes_types import NotesTemplateType


def test_block_absent_when_unavailable():
    assert _render_source_html_block(False) is None


def test_block_present_and_shaped_when_available():
    block = _render_source_html_block(True)
    assert block is not None
    assert "read_source_note" in block
    assert "PDF wins" in block  # source is a reference, PDF is ground truth

    # VERBATIM PASSTHROUGH (2026-07-19). The block previously told the agent to
    # translate each source `style=` into a `format_ops` entry; that round-trip
    # through model judgement was the "AI guessing the formatting" reported in
    # run 74. Tables are now copied byte-for-byte into `content`.
    assert "VERBATIM" in block.upper()
    assert "style=" in block  # the attribute is copied, not re-described
    lowered = block.lower()
    assert "do not" in lowered and "translate" in lowered
    # format_ops survives ONLY as the PDF-only fallback, never the table path.
    assert "format_ops" in block
    assert "fallback" in lowered
    # Gotcha #16 is reversed for tables ONLY — prose must stay style-free.
    assert "prose stays style-free" in lowered


def test_render_notes_prompt_gates_on_flag():
    kwargs = dict(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )
    with_src = render_notes_prompt(**kwargs, source_html_available=True)
    without = render_notes_prompt(**kwargs, source_html_available=False)
    assert "SOURCE DOCUMENT FORMATTING" in with_src
    assert "SOURCE DOCUMENT FORMATTING" not in without


def _tool_names(agent) -> set[str]:
    # Mirror tests/test_notes_agent_factory.py's version-stable accessor.
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None:
            continue
        tools = getattr(ts, "tools", None)
        if tools is None:
            continue
        if isinstance(tools, dict):
            names = {getattr(t, "name", None) or k for k, t in tools.items()}
        else:
            names = {getattr(t, "name", None) for t in tools}
        return {n for n in names if n}
    return set()


def _make_agent(pdf_path: str):
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=pdf_path,
        inventory=[],
        filing_level="company",
        model="test",
    )
    return agent, deps


def test_tool_registered_only_with_sidecar(tmp_path: Path):
    # No sidecar → tool absent, deps path None.
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    agent_no, deps_no = _make_agent(str(tmp_path / "uploaded.pdf"))
    assert "read_source_note" not in _tool_names(agent_no)
    assert deps_no.source_html_path is None

    # With sidecar → tool present, deps path set.
    with_dir = tmp_path / "withsrc"
    with_dir.mkdir()
    (with_dir / "uploaded.pdf").write_bytes(b"%PDF")
    (with_dir / "source.html").write_text("<h1>4. X</h1><p>y</p>", encoding="utf-8")
    agent_yes, deps_yes = _make_agent(str(with_dir / "uploaded.pdf"))
    assert "read_source_note" in _tool_names(agent_yes)
    assert deps_yes.source_html_path == str(with_dir / "source.html")


# --- unconsulted-source nudge (2026-07-19) ---------------------------------
# Run 74: the Accounting Policies agent never called read_source_note, so its
# tables were rebuilt from the PDF while its peers copied real Word markup.

def test_unconsulted_source_nudge_is_silent_at_zero():
    from notes.agent import format_unconsulted_source_nudge

    assert format_unconsulted_source_nudge(0) == ""
    assert format_unconsulted_source_nudge(-1) == ""


def test_unconsulted_source_nudge_invites_a_resend_without_demanding_one():
    from notes.agent import format_unconsulted_source_nudge

    msg = format_unconsulted_source_nudge(3)
    assert "read_source_note" in msg
    assert "3 table cell(s)" in msg
    # Never pushes the agent to invent formatting when the source has none.
    assert "no action is needed" in msg.lower()


_PN = {"number": "5", "title": "Test note"}


def test_payload_consulted_helper_tracks_note_refs():
    from notes.agent import NotesDeps, _payload_source_consulted
    from notes.payload import NotesPayload

    deps = NotesDeps(
        pdf_path="x", template_path="y", model=None, output_dir="z",
        token_report=None, template_type=None, sheet_name="s",
        filing_level="company",
    )
    deps.consulted_source_notes = {5}
    p_hit = NotesPayload(chosen_row_label="a", content="<table></table>",
                         evidence="e", note_num=5, parent_note=_PN)
    p_miss = NotesPayload(chosen_row_label="a", content="<table></table>",
                          evidence="e", note_num=7, parent_note={"number": "7", "title": "Other"})
    p_sub = NotesPayload(chosen_row_label="a", content="<table></table>",
                         evidence="e", source_note_refs=["5.1"], parent_note=_PN)
    p_none = NotesPayload(chosen_row_label="a", content="<table></table>",
                          evidence="e", parent_note=_PN)
    assert _payload_source_consulted(deps, p_hit)
    assert not _payload_source_consulted(deps, p_miss)
    assert _payload_source_consulted(deps, p_sub)  # "5.1" -> parent note 5
    # parent_note number alone is enough to resolve the note (it is
    # mandatory on any content payload), and note 5 was consulted.
    assert _payload_source_consulted(deps, p_none)


# --- uncopied-source nudge (2026-08-04) ------------------------------------
# Run 79: every Sheet-12 sub-agent CALLED read_source_note and every call
# returned styled markup, yet 14 table cells persisted with no styling — the
# tables were rebuilt from the PDF rather than copied. Neither existing nudge
# covered that: the unconsulted one needs an unread source, and the run-63 one
# asks for format_ops, which the source block explicitly forbids on a Word run.

def test_uncopied_source_nudge_is_silent_at_zero():
    from notes.agent import format_uncopied_source_nudge

    assert format_uncopied_source_nudge(0) == ""
    assert format_uncopied_source_nudge(-1) == ""


def test_uncopied_source_nudge_names_copying_and_blesses_the_ops_fallback():
    from notes.agent import format_uncopied_source_nudge

    msg = format_uncopied_source_nudge(2)
    assert "2 table cell(s)" in msg
    assert "read_source_note" in msg
    lowered = msg.lower()
    # The remedy is the COPY, and the message must not send the agent back to
    # describing the styling as ops — that contradiction is the defect.
    assert "verbatim" in lowered
    assert "do not describe the styling as format_ops" in lowered
    # Still two-sided: ops remain correct for a note the source doesn't cover.
    assert "no table" in lowered


def _make_word_sink_agent(tmp_path: Path, *, with_source: bool):
    """Sheet-12 sub-agent in sink mode, with or without a Word sidecar."""
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF-1.4\n")
    if with_source:
        (tmp_path / "source.html").write_text(
            "<h1>1. Corporate information</h1>"
            '<table><tr><td style="text-align: right">1,595</td></tr></table>',
            encoding="utf-8",
        )
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        batch_note_nums=[1],
    )
    deps.payload_sink = []
    return agent, deps


def _write_plain_table(agent, deps):
    """Write one plain (unstyled) table for note 1 and return the tool reply."""
    import asyncio
    import json
    from types import SimpleNamespace

    from notes.agent import _ensure_label_index

    label = _ensure_label_index(deps)[0].original
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is not None and isinstance(getattr(ts, "tools", None), dict):
            fn = ts.tools["write_notes"].function
            break
    else:  # pragma: no cover - toolset accessor drift
        raise AssertionError("write_notes tool not found")
    payloads_json = json.dumps({"payloads": [{
        "chosen_row_label": label,
        "content": "<table><tr><td>1,595</td></tr></table>",
        "evidence": "Page 3, Note 1",
        "source_pages": [3],
        "note_num": 1,
        "parent_note": {"number": "1", "title": "Corporate information"},
    }]})
    return asyncio.run(fn(SimpleNamespace(deps=deps), payloads_json))


def test_word_run_consulted_table_gets_the_copy_nudge_not_format_ops(tmp_path: Path):
    """The run-79 case: source read, table still plain. The agent must be told
    to copy the markup it already fetched — NOT to re-describe it as ops."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    deps.consulted_source_notes = {1}

    msg = _write_plain_table(agent, deps)
    assert msg.startswith("Collected 1 payload")  # prefix contract (gotcha #7)
    assert "already called read_source_note" in msg
    # The contradiction is gone: no format_ops demand on a Word run.
    assert "without format_ops" not in msg
    # And the unconsulted nudge stays quiet — this agent DID read the source.
    assert "written without calling read_source_note" not in msg


def test_word_run_unconsulted_table_still_gets_the_consult_nudge(tmp_path: Path):
    """Run 74's case is unchanged: never read the source → consult nudge only."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    deps.consulted_source_notes = set()

    msg = _write_plain_table(agent, deps)
    assert "written without calling read_source_note" in msg
    assert "already called read_source_note" not in msg


class _FakeResult:
    """Minimal stand-in for the writer's NotesWriteResult."""

    def __init__(self, cells_written, fuzzy_matches=()):
        self.cells_written = list(cells_written)
        self.fuzzy_matches = list(fuzzy_matches)


def _deps_with_source(consulted=()):
    from notes.agent import NotesDeps

    deps = NotesDeps(
        pdf_path="x", template_path="y", model=None, output_dir="z",
        token_report=None, template_type=None, sheet_name="s",
        filing_level="company", source_html_path="/tmp/source.html",
    )
    deps.consulted_source_notes = set(consulted)
    return deps


def _payload(label, note, content="<table><tr><td>1</td></tr></table>"):
    from notes.payload import NotesPayload

    return NotesPayload(
        chosen_row_label=label, content=content, evidence="Page 1",
        parent_note={"number": str(note), "title": "N"},
    )


def test_word_nudges_ignore_payloads_the_writer_rejected():
    """Peer review 2026-08-04: the direct-write path counted SUBMITTED payloads,
    so a rejected label or a failed row write was still reported as a cell that
    "landed unstyled" — sending the agent to restyle a cell that was never
    written. All-rejected is the sharp case: zero cells, and the nudge fired
    anyway."""
    from notes.agent import word_run_nudge_counts

    deps = _deps_with_source(consulted={5})
    payloads = [_payload("Row A", 5)]
    # The writer refused it: no cells_written entry.
    assert word_run_nudge_counts(deps, payloads, _FakeResult([])) == (0, 0)


def test_word_nudges_count_only_the_cell_that_landed():
    """Mixed success: one payload written, one rejected. Only the written one
    may be nudged about."""
    from notes.agent import word_run_nudge_counts

    deps = _deps_with_source(consulted={5, 6})
    payloads = [_payload("Row A", 5), _payload("Row B", 6)]
    result = _FakeResult([
        {"label": "Row A", "html": "<table><tr><td>1</td></tr></table>",
         "style_source": "unstyled"},
    ])
    assert word_run_nudge_counts(deps, payloads, result) == (0, 1)


def test_word_nudges_follow_a_fuzzy_matched_label_to_its_cell():
    """A fuzzy-but-accepted label is the same row, so the payload must still be
    attributed to the cell the writer created under the TEMPLATE label."""
    from notes.agent import word_run_nudge_counts

    deps = _deps_with_source(consulted=set())  # never read the source
    payloads = [_payload("Row A (approx)", 5)]
    result = _FakeResult(
        [{"label": "Row A", "html": "<table><tr><td>1</td></tr></table>",
          "style_source": "unstyled"}],
        fuzzy_matches=[("Row A (approx)", "Row A", 0.91)],
    )
    # Unconsulted, so it belongs to the run-74 bucket, not the run-79 one.
    assert word_run_nudge_counts(deps, payloads, result) == (1, 0)


def test_word_nudges_count_a_combined_cell_once():
    """`_combine_payloads` folds several payloads into ONE cell. Both messages
    say "N table cell(s)", so a note written in parts must count once."""
    from notes.agent import word_run_nudge_counts

    deps = _deps_with_source(consulted={5})
    payloads = [_payload("Row A", 5), _payload("Row A", 5)]
    result = _FakeResult([
        {"label": "Row A", "html": "<table><tr><td>1</td></tr></table>",
         "style_source": "unstyled"},
    ])
    assert word_run_nudge_counts(deps, payloads, result) == (0, 1)


def test_word_nudges_stay_silent_for_styled_and_prose_cells():
    from notes.agent import word_run_nudge_counts

    deps = _deps_with_source(consulted={5, 6})
    payloads = [_payload("Row A", 5), _payload("Row B", 6, "<p>Prose.</p>")]
    result = _FakeResult([
        # Copied verbatim from the source — nothing to nudge.
        {"label": "Row A", "html": "<table><tr><td>1</td></tr></table>",
         "style_source": "source"},
        # Prose carries no table.
        {"label": "Row B", "html": "<p>Prose.</p>", "style_source": "unstyled"},
    ])
    assert word_run_nudge_counts(deps, payloads, result) == (0, 0)


# --- source-copy replaces the draft (run-79 duplication, 2026-08-05) -------
# Notes 6 and 9 each shipped TWICE in one cell: the agent wrote a rebuilt
# table, the nudge invited a source-copied re-send, and the sink's
# identical-content rule couldn't match the two versions (the rebuild
# compresses headers), so the combine path concatenated them. A source copy
# is a whole-note copy, so it must REPLACE earlier payloads for that note.

_PLAIN_TABLE = "<p>Intro.</p><table><tr><td>Rebuilt</td><td>1,000</td></tr></table>"
_SOURCE_TABLE = (
    '<p>Intro.</p><table><tr>'
    '<td style="border-bottom: 1px solid #000000">Property, plant and equipment</td>'
    '<td style="text-align: right">1,000</td></tr></table>'
)


def _send_payload(agent, deps, label, note, content, parent_number=None):
    """One write_notes call carrying a single payload for `note` at `label`."""
    import asyncio
    import json
    from types import SimpleNamespace

    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is not None and isinstance(getattr(ts, "tools", None), dict):
            fn = ts.tools["write_notes"].function
            break
    else:  # pragma: no cover - toolset accessor drift
        raise AssertionError("write_notes tool not found")
    payloads_json = json.dumps({"payloads": [{
        "chosen_row_label": label,
        "content": content,
        "evidence": f"Page 3, Note {note}",
        "source_pages": [3],
        "note_num": note,
        "parent_note": {
            "number": parent_number or str(note), "title": "T",
        },
    }]})
    return asyncio.run(fn(SimpleNamespace(deps=deps), payloads_json))


def test_source_copy_resend_replaces_the_rebuilt_draft(tmp_path: Path):
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    deps.consulted_source_notes = {1}
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    _send_payload(agent, deps, label, 1, _PLAIN_TABLE)
    assert len(deps.payload_sink) == 1
    _send_payload(agent, deps, label, 1, _SOURCE_TABLE)
    # Replaced, not concatenated — ONE payload, the source-styled one.
    assert len(deps.payload_sink) == 1
    assert "style=" in deps.payload_sink[0].content


def test_source_copy_subsumes_a_multipart_draft(tmp_path: Path):
    """A note sent in parts (prose payload + table payload) is one note; the
    whole-note source copy replaces BOTH parts, not just the matching one."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    _send_payload(agent, deps, label, 1, "<p>Part one prose.</p>")
    _send_payload(agent, deps, label, 1, _PLAIN_TABLE)
    assert len(deps.payload_sink) == 2  # parts combine — today's semantics
    _send_payload(agent, deps, label, 1, _SOURCE_TABLE)
    assert len(deps.payload_sink) == 1
    assert "style=" in deps.payload_sink[0].content


def test_plain_resend_with_different_text_still_combines(tmp_path: Path):
    """The replace rule is for SOURCE copies only. Two plain payloads with
    genuinely different text keep the combine semantics — a note written in
    parts must not lose part one."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    _send_payload(agent, deps, label, 1, _PLAIN_TABLE)
    _send_payload(agent, deps, label, 1, "<p>More prose for the note.</p>")
    assert len(deps.payload_sink) == 2


def test_source_copy_keeps_the_other_note_on_a_shared_row(tmp_path: Path):
    """Replacement is scoped to the SAME top-level note. A different note
    sharing the row (deliberate grouping) must survive the re-send."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    _send_payload(agent, deps, label, 1, _PLAIN_TABLE)
    _send_payload(agent, deps, label, 2, "<p>Note 2 disclosure.</p>")
    _send_payload(agent, deps, label, 1, _SOURCE_TABLE)
    notes = sorted(str(p.parent_note["number"]) for p in deps.payload_sink)
    assert notes == ["1", "2"]


def test_mixed_note_row_draws_an_advisory_warning(tmp_path: Path):
    """Run 79's Note 9 cell shipped with unrelated Note 22.1 inside it and
    nothing said so at write time. Mixing is a warning, never a reject."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    msg1 = _send_payload(agent, deps, label, 9, _PLAIN_TABLE)
    assert "now holds notes" not in msg1
    msg2 = _send_payload(agent, deps, label, 22, "<p>Note 22.1 text.</p>",
                         parent_number="22.1")
    assert "now holds notes 9 and 22" in msg2
    assert msg2.startswith("Collected 1 payload")  # prefix contract intact
    assert len(deps.payload_sink) == 2  # advisory — nothing was rejected


def test_subnote_of_the_same_note_does_not_warn(tmp_path: Path):
    """Note 9 + sub-note 9.1 in one row is the normal grouped shape."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=True)
    from notes.agent import _ensure_label_index
    label = _ensure_label_index(deps)[0].original

    _send_payload(agent, deps, label, 9, _PLAIN_TABLE)
    msg = _send_payload(agent, deps, label, 9, "<p>Sub-note detail.</p>",
                        parent_number="9.1")
    assert "now holds notes" not in msg


def test_pdf_run_keeps_the_run63_format_ops_nudge(tmp_path: Path):
    """No Word source → format_ops IS the right remedy. PDF runs must be
    byte-identical to before the run-79 change."""
    agent, deps = _make_word_sink_agent(tmp_path, with_source=False)
    assert deps.source_html_path is None

    msg = _write_plain_table(agent, deps)
    assert "without format_ops" in msg
    assert "truly plain" in msg  # the no-invention escape hatch
    assert "already called read_source_note" not in msg
