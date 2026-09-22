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


def _word_upload_dir(tmp_path):
    """A session dir shaped like a Word upload: uploaded.pdf + source.html."""
    d = tmp_path / "session"
    d.mkdir()
    (d / "uploaded.pdf").write_bytes(b"%PDF-1.4\n")
    (d / "source.html").write_text(
        "<h3>5. Receivables</h3><p>Stated at cost.</p>", encoding="utf-8",
    )
    return d


def test_block_mode_hides_the_copy_workflow_tool(tmp_path, seeded):
    """Peer review 2026-08-06: the prompt switched to block assembly but
    read_source_note stayed registered, and its description teaches
    copy-into-content — two incompatible workflows exposed at once. On a
    block-path run the copy tool must be absent."""
    from pydantic_ai.models.test import TestModel

    db, run_id, gen = seeded
    d = _word_upload_dir(tmp_path)
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(d / "uploaded.pdf"),
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
        run_id=run_id, db_path=db, source_generation_id=gen,
    )
    names = _tool_names(agent)
    assert deps.source_block_notes == {5, 6}
    assert "write_note_from_source" in names
    assert "read_source_note" not in names


def test_sidecar_only_run_keeps_the_copy_workflow_tool(tmp_path):
    """No generation → the sidecar workflow is unchanged, copy tool and all."""
    from pydantic_ai.models.test import TestModel

    d = _word_upload_dir(tmp_path)
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(d / "uploaded.pdf"),
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
    )
    names = _tool_names(agent)
    assert deps.source_block_notes == set()
    assert "read_source_note" in names
    assert "write_note_from_source" not in names


def test_numeric_templates_stay_off_the_block_workflow(tmp_path, seeded):
    """Peer review 2026-08-06: sheets 13/14 need structured numeric_values and
    write_note_from_source resolves prose nodes only — teaching it there
    taught a write that always rejects. A numeric agent keeps the sidecar
    workflow: no write tool, no block prompt, copy tool still present."""
    from pydantic_ai.models.test import TestModel

    db, run_id, gen = seeded
    d = _word_upload_dir(tmp_path)
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        pdf_path=str(d / "uploaded.pdf"),
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
        run_id=run_id, db_path=db, source_generation_id=gen,
    )
    names = _tool_names(agent)
    assert deps.source_block_notes == set()
    assert "write_note_from_source" not in names
    assert "read_source_note" in names


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


def test_prepared_source_rejects_prose_fallback_even_without_numbered_notes(tmp_path, seeded):
    import asyncio
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel

    db, run_id, gen = seeded
    with repo.db_session(db) as conn:
        conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
        conn.execute("UPDATE notes_source_notes SET top_note_num='' WHERE generation_id=?", (gen,))
        conn.commit()
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(), output_dir=str(tmp_path),
        run_id=run_id, db_path=db, source_generation_id=gen)
    assert deps.prepared_source_required
    assert "write_note_from_source" in _tool_names(agent)
    assert "write_notes" not in _tool_names(agent)
    assert "report_source_gap" in _tool_names(agent)
    prompt = notes_agent.render_notes_prompt(NotesTemplateType.CORP_INFO, "company", [],
        source_blocks_available=True, prepared_source_required=True)
    assert "All writes go through" not in prompt
    assert "copy its markup by hand" not in prompt
    assert "report_source_gap" in prompt


def test_manifest_accepts_stable_identity_for_unnumbered_note(seeded):
    db, _, gen = seeded
    with repo.db_session(db) as conn:
        conn.execute("UPDATE notes_source_notes SET top_note_num='' WHERE generation_id=? AND source_note_id='n5'", (gen,))
        conn.commit()
    assert "b1" in notes_agent._read_source_manifest_impl(db, gen, "n5")
    assert "b2" in notes_agent._read_source_manifest_impl(db, gen, "n5")


@pytest.mark.asyncio
async def test_prepared_capture_gap_is_persisted_without_false_coverage(tmp_path, seeded, monkeypatch):
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    db, run_id, gen = seeded
    with repo.db_session(db) as conn:
        conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    monkeypatch.setattr("tools.pdf_viewer.count_pdf_pages", lambda path: 2)
    agent, deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES, pdf_path="synthetic.pdf",
        inventory=[], filing_level="company", model=TestModel(), output_dir=str(tmp_path),
        run_id=run_id, db_path=db, source_generation_id=gen, batch_note_nums=[5])
    deps.payload_sink = []
    report = next(ts.tools["report_source_gap"].function for ts in agent.toolsets
                  if "report_source_gap" in getattr(ts, "tools", {}))
    result = report(SimpleNamespace(deps=deps), [1], "Table heading was not captured", 5)
    assert "human review" in result
    assert "accepted" in notes_agent._submit_coverage_entries_impl(deps, [])
    assert deps.coverage_receipt.entries == []  # It did not become covered.
    with repo.db_session(db) as conn:
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert len(flags) == 1
    assert flags[0]["reason"] == "Table heading was not captured"
    assert flags[0]["source_pages"] == [1]
    # Saving the report does not claim that missing source content was written.
    deps.payload_sink = None
    save = next(ts.tools["save_result"].function for ts in agent.toolsets
                if "save_result" in getattr(ts, "tools", {}))
    await save(SimpleNamespace(deps=deps))
    assert deps.source_gap_reported
    assert not deps.wrote_once
    assert deps.cells_written == []
    assert deps.write_skip_errors


def test_source_collision_is_persisted_with_actionable_agent_feedback(seeded):
    from types import SimpleNamespace
    from notes import source_write

    db, run_id, gen = seeded
    with repo.db_session(db) as conn:
        for row, label in ((10, "Income tax"), (11, "Deferred tax")):
            conn.execute(
                "INSERT INTO notes_nodes(node_uuid,template_id,sheet,row,label,kind) "
                "VALUES(?,?,?,?,?,'LEAF')",
                (f"policy-{row}", "mfrs-company-policies-v1",
                 "Notes-SummaryofAccPol", row, label),
            )
        source_write.write_cell_from_blocks(
            conn,
            run_id=run_id,
            generation_id=gen,
            sheet="Notes-SummaryofAccPol",
            row=10,
            block_ids=["b1", "b2"],
            template_prefix="mfrs-company-",
        )
    deps = SimpleNamespace(
        db_path=db,
        run_id=run_id,
        source_generation_id=gen,
        filing_standard="mfrs",
        filing_level="company",
        sheet_name="Notes-SummaryofAccPol",
        source_placement_conflict_notes=set(),
        write_skip_errors=[],
    )

    result = notes_agent._write_from_source_impl(
        deps,
        "Notes-SummaryofAccPol",
        11,
        ["b1", "b2"],
        [1],
        "page 1",
        None,
    )

    assert "conflict recorded for review" in result
    assert "Income tax" in result and "Deferred tax" in result
    assert "provisional" in result
    assert deps.source_placement_conflict_notes == {5}
    with repo.db_session(db) as conn:
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert len(flags) == 1
    assert flags[0]["finding_id"].startswith('["source_placement",')
    assert flags[0]["source_pages"] == [1]


def test_source_tools_identify_reconstructed_content_without_requiring_repair(seeded):
    db, _, gen = seeded
    with repo.db_session(db) as conn:
        conn.execute("UPDATE notes_source_blocks SET locator_json=? WHERE generation_id=? AND block_id='b2'",
                     ('{"capture_uncertain":true,"capture_method":"reconstructed"}', gen))
        conn.commit()
    result = notes_agent._view_source_blocks_impl(db, gen, ["b2"])
    assert "best-effort reconstruction" in result
    assert "use captured content" in result
    assert "Stated at cost." in result
