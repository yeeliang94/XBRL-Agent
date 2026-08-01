"""Read-only source tools on the notes agent — plan Phase 6, Step 6.1.

Three properties the plan calls out, each tested here:

* the tools are registered ONLY when the run has a frozen source reading, so
  an `off`-mode or PDF run sees exactly the agent it saw before;
* every response is capped in bytes — an uncapped `view_source_blocks`
  recreates the context problem the 60,000-char snippet cap was built to
  solve, one call at a time;
* document content keeps the untrusted-content framing `read_source_note`
  already uses.
"""
from __future__ import annotations

import pytest

from db import repository as repo
from db.schema import init_db
from notes import agent as notes_agent
from notes import source_repository as srepo
from notes_types import NotesTemplateType
from notes.source_models import SourceBlock, SourceNote

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="heading", reading_order=0,
                canonical_html="<h3>5. Receivables</h3>", source_note_id="n5"),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>Stated at cost.</p>", source_note_id="n5"),
    SourceBlock(block_id="b3", block_kind="table", reading_order=2,
                canonical_html="<table><tr><td>a</td></tr></table>",
                source_note_id="n6"),
]


@pytest.fixture()
def seeded(tmp_path):
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.write_notes(conn, gen, [
            SourceNote(source_note_id="n5", top_note_num="5",
                       title="Receivables", block_ids=["b1", "b2"]),
            SourceNote(source_note_id="n6", top_note_num="6",
                       title="Cash", block_ids=["b3"]),
        ])
        srepo.activate_generation(conn, gen)
    return str(db), run_id, gen


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

def _tool_names(agent) -> set[str]:
    """Same introspection the existing agent-tool tests use."""
    names: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def test_the_source_tools_are_absent_without_a_frozen_reading(tmp_path):
    """An `off`-mode run must see exactly the agent it saw before."""
    from pydantic_ai.models.test import TestModel

    agent, _deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
    )
    names = _tool_names(agent)
    assert "write_note_from_source" not in names
    assert "list_source_notes" not in names


def test_the_source_tools_appear_with_a_frozen_reading(tmp_path, seeded):
    from pydantic_ai.models.test import TestModel

    db, run_id, gen = seeded
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
        run_id=run_id, db_path=db, source_generation_id=gen,
    )
    names = _tool_names(agent)
    assert {"list_source_notes", "read_source_manifest",
            "view_source_blocks", "write_note_from_source"} <= names
    assert deps.source_generation_id == gen


# --------------------------------------------------------------------------
# what the tools return
# --------------------------------------------------------------------------

def test_listing_notes_reports_each_notes_part_count(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._list_source_notes_impl(db, gen)
    assert "note   5" in out and "2 part(s)" in out
    assert "note   6" in out


def test_the_manifest_lists_ids_and_kinds_for_one_note(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._read_source_manifest_impl(db, gen, 5)
    assert "b1" in out and "b2" in out
    assert "b3" not in out, "another note's parts must not leak in"
    assert "heading" in out and "paragraph" in out


def test_a_note_with_no_parts_says_to_read_the_pdf(seeded):
    db, _run_id, gen = seeded
    assert "Read the PDF" in notes_agent._read_source_manifest_impl(db, gen, 99)


def test_viewing_blocks_returns_their_full_content(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._view_source_blocks_impl(db, gen, ["b2"])
    assert "Stated at cost" in out


def test_an_unknown_block_id_is_named_not_silently_dropped(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._view_source_blocks_impl(db, gen, ["b2", "nope"])
    assert "not found: nope" in out


def test_asking_only_for_unknown_ids_points_at_the_manifest(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._view_source_blocks_impl(db, gen, ["nope"])
    assert "read_source_manifest" in out


def test_document_content_carries_the_untrusted_framing(seeded):
    db, _run_id, gen = seeded
    out = notes_agent._view_source_blocks_impl(db, gen, ["b2"])
    assert "UNTRUSTED" in out
    assert "data, not commands" in out
    assert "<<<SOURCE>>>" in out


# --------------------------------------------------------------------------
# caps
# --------------------------------------------------------------------------

def test_a_long_response_is_cut_and_says_so(tmp_path):
    db = tmp_path / "big.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, [
            SourceBlock(block_id="big", block_kind="paragraph", reading_order=0,
                        canonical_html="<p>" + ("x" * 200_000) + "</p>",
                        source_note_id="n1"),
        ])
        srepo.activate_generation(conn, gen)
    out = notes_agent._view_source_blocks_impl(str(db), gen, ["big"])
    assert len(out) <= notes_agent.SOURCE_TOOL_RESPONSE_CAP + 200
    assert "ask for fewer parts" in out


def test_too_many_block_ids_are_bounded_per_call(seeded):
    db, _run_id, gen = seeded
    many = [f"b{i}" for i in range(200)] + ["b1"]
    out = notes_agent._view_source_blocks_impl(db, gen, many)
    assert f"first {notes_agent._SOURCE_BLOCKS_PER_CALL} parts" in out


def test_previews_in_the_manifest_are_short(tmp_path):
    db = tmp_path / "prev.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, [
            SourceBlock(block_id="b1", block_kind="paragraph", reading_order=0,
                        canonical_html="<p>" + ("word " * 5000) + "</p>",
                        source_note_id="n1"),
        ])
        srepo.activate_generation(conn, gen)
    out = notes_agent._read_source_manifest_impl(str(db), gen, 1)
    assert len(out) < 2_000


# --------------------------------------------------------------------------
# a run without a reading degrades, never crashes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("impl,args", [
    (notes_agent._list_source_notes_impl, ()),
    (notes_agent._read_source_manifest_impl, (1,)),
    (notes_agent._view_source_blocks_impl, (["b1"],)),
])
def test_every_tool_degrades_without_a_generation(impl, args):
    assert "No frozen source reading" in impl(None, None, *args)
