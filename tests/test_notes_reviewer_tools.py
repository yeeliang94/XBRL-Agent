"""Notes-reviewer write tools + title detector + packet (docs/PLAN.md Steps 5,7,8).

The tools are agent closures, so they're exercised by driving the real agent
with a scripted FunctionModel (view a page, then call a write tool). Asserts:
  - tools mutate ONLY notes_cells (DB), never an xlsx;
  - author/move into an occupied or ABSTRACT row is refused;
  - edit preserves the writer-owned leading <h3>;
  - move re-routes prose and clears the source.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det  # _render_single_page (PDF render) lives here now
from db import repository as repo
from db.schema import init_db

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    # 30-page PDF; rendering returns a stub image so view_pdf_pages records
    # the page into viewed_pages without a real file.
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 30)
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")


def _seed_node(db_path: Path, row: int, kind: str, label: str) -> None:
    slot_role = "INPUT" if kind == "LEAF" else "PRESENTATION_ONLY"
    with repo.db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind, slot_role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"n{row}", f"{_PREFIX}notes-listofnotes-v1", _S12, row,
                label, kind, slot_role,
            ),
        )


def _seed_inventory(db_path: Path, run_id: int, note_num: int) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=note_num)


def _seed_cell(db_path: Path, run_id: int, row: int, html: str) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S12, row=row, label=f"Row {row}", html=html,
        )


def _scripted(steps: list[list]) -> FunctionModel:
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]
        idx["i"] += 1
        if i < len(steps):
            return ModelResponse(parts=steps[i])
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(fn)


def _agent(db_path: Path, run_id: int, model):
    return ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=model, output_dir=str(db_path.parent),
    )


def _cells(db_path: Path, run_id: int) -> dict[int, str]:
    with repo.db_session(db_path) as conn:
        return {c.row: c.html for c in repo.list_notes_cells_for_run(conn, run_id)}


def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for toolset in getattr(agent, "toolsets", []) or []:
        names.update(getattr(toolset, "tools", {}) or {})
    return names


# --------------------------------------------------------------------------


def test_source_reviewer_tools_are_hidden_without_frozen_source(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    agent, _deps, _ = _agent(db_path, run_id, _scripted([]))
    names = _tool_names(agent)
    assert "relink_note_cell" not in names
    assert "record_block_dispositions" not in names
    assert "FROZEN SOURCE MODE" not in agent._system_prompts[0]


def test_source_reviewer_tools_and_instructions_appear_together(db_path: Path) -> None:
    from notes import source_repository as srepo
    from notes.source_models import OwnerKind, SourceBlock

    run_id = _seed_run(db_path)
    with repo.db_session(db_path) as conn:
        generation_id = srepo.begin_generation(
            conn, run_id, input_kind="docx_html"
        )
        srepo.write_blocks(conn, generation_id, [SourceBlock(
            block_id="b1", block_kind="paragraph", reading_order=0,
            canonical_html="<p>Source disclosure.</p>",
            owner_kind=OwnerKind.NOTE,
        )])
        srepo.activate_generation(conn, generation_id)

    agent, deps, _ = _agent(db_path, run_id, _scripted([]))
    names = _tool_names(agent)
    assert {"relink_note_cell", "record_block_dispositions"} <= names
    assert deps.source_generation_id == generation_id
    prompt = agent._system_prompts[0]
    assert "FROZEN SOURCE MODE" in prompt
    assert "relink_note_cell" in prompt


def test_source_packet_groups_repeated_missing_parts_without_losing_ids():
    context = {
        "source_integrity_findings": [
            {"check": "disposition", "code": "missing_disposition_decision",
             "note_num": "note-2", "block_ids": [bid],
             "message": f"source part {bid} lacks a decision"}
            for bid in ("p17-b1", "p17-b2", "p18-b1")
        ],
    }
    packet = ra.build_notes_reviewer_packet(context)
    assert packet.count("has no recognised decision recorded") == 1
    assert all(bid in packet for bid in ("p17-b1", "p17-b2", "p18-b1"))


def test_source_packet_defers_findings_caused_by_open_placement_conflict():
    context = {
        "placement_conflicts": [{
            "ref": "C1", "match_kind": "same_field",
            "target": {"sheet": _S12, "row": 140, "label": "Other receivables"},
            "proposed_block_ids": ["p31-b2", "p31-b3"],
        }],
        "source_integrity_findings": [
            {"check": "disposition", "code": "missing_disposition_decision",
             "note_num": "12", "block_ids": ["p31-b2", "p31-b3"],
             "message": "source parts lack a decision"},
            {"check": "disposition", "code": "missing_disposition_decision",
             "note_num": "13", "block_ids": ["p32-b1"],
             "message": "another source part lacks a decision"},
        ],
    }
    packet = ra.build_notes_reviewer_packet(context)
    assert "1 source-completeness finding(s) depend" in packet
    assert "Do not flag the same parts twice" in packet
    assert "p32-b1" in packet
    assert "source parts lack a decision" not in packet
    assert ra.count_open_items(context) == 3
    assert ra.count_review_work_items(context) == 2


def test_large_conflict_packet_preserves_destinations_and_read_action():
    packet = ra.build_notes_reviewer_packet({"placement_conflicts": [{
        "ref": "C1", "block_ids": [f"p22-b{i}-abcdef" for i in range(100)],
        "source_notes": ["12"],
        "existing": [{"sheet": _S12, "row": 49, "label": "Existing field"}],
        "target": {"sheet": _S12, "row": 80, "label": "Proposed field"},
    }]})
    assert "Existing field" in packet and "Proposed field" in packet
    assert "100" in packet and "read_source_manifest" in packet


def test_prepared_reviewer_counts_independent_work_without_dropping_checks():
    from notes.coverage_checklist import Checklist, CoverageRow, SubNoteState

    blocks = [f"p17-b{i}" for i in range(1, 5)] + ["p32-b3", "p32-b4"]
    context = {
        "placement_conflicts": [
            {"ref": "C1", "proposed_block_ids": blocks[:4]},
            {"ref": "C2", "proposed_block_ids": blocks[4:]},
        ],
        "source_integrity_findings": [
            {"block_ids": [bid]} for bid in blocks
        ] + [{"block_ids": blocks[:4]}, {"block_ids": blocks[4:]}],
        "policy_placements": [
            {"row": row, "label": "policy", "preview": "source"}
            for row in (9, 22, 27, 28, 32, 33, 42, 49, 57)
        ],
        "coverage_gaps": [12],
        "coverage_checklist": Checklist(rows=[
            CoverageRow(12, "Other receivables", "missing"),
            CoverageRow(20, "Other note", "placed", subnotes=[
                SubNoteState("(a)", "not_verified"),
            ]),
        ]),
    }
    assert ra.count_open_items(context) == 22
    assert ra.count_review_work_items(context) == 13
    packet = ra.build_notes_reviewer_packet(context)
    assert "8 source-completeness finding(s) depend" in packet
    assert "then call verify_findings" in packet
    context["placement_conflicts"] = context["placement_conflicts"][1:]
    assert ra.count_review_work_items(context) == 17
    assert "[SOURCE COMPLETENESS]" in ra.build_notes_reviewer_packet(context)
    assert ra.count_review_work_items({"coverage_gaps": [12]}) == 1


def test_prepared_policy_placement_requires_grounded_current_verdict(db_path: Path) -> None:
    from types import SimpleNamespace
    from notes import source_repository as srepo
    from notes.reviewer_agent import PlacementVerificationItem, unverified_policy_placements
    from notes.source_models import SourceBlock, OwnerKind

    run_id = _seed_run(db_path)
    sheet = ra.POLICIES_SHEET
    with repo.db_session(db_path) as conn:
        generation_id = srepo.begin_generation(conn, run_id, input_kind="prepared_document")
        srepo.write_blocks(conn, generation_id, [SourceBlock(
            block_id="policy", block_kind="paragraph", reading_order=0,
            canonical_html="<p>Inventory policy.</p>",
            owner_kind=OwnerKind.NOTE, source_note_id="n4",
        )])
        srepo.activate_generation(conn, generation_id)
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=33,
            label="Description of accounting policy for income tax",
            html="<h3>4. Inventories</h3><p>Inventory policy.</p>")
        conn.execute("UPDATE notes_cells SET source_generation_id=? WHERE run_id=? AND sheet=? AND row=33",
            (generation_id, run_id, sheet))
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    assert context["policy_placements"][0]["row"] == 33
    assert "POLICY DESTINATION ACCURACY" in ra.build_notes_reviewer_packet(context)
    assert unverified_policy_placements(deps) == [33]
    funcs = {name: tool.function for ts in agent.toolsets
             for name, tool in getattr(ts, "tools", {}).items()}
    item = PlacementVerificationItem(row=33,
        target_label="Description of accounting policy for income tax",
        verdict="needs_human", reason="The source is an inventories policy.",
        source_pages=[20])
    ctx = SimpleNamespace(deps=deps)
    assert "rejected" in funcs["verify_policy_placements"](ctx, [item])
    deps.viewed_pages.add(20)
    assert "needs_human" in funcs["verify_policy_placements"](ctx, [item])
    assert unverified_policy_placements(deps) == []
    assert len(deps.flags) == 1
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=33,
            label=item.target_label, html="<h3>4. Inventories</h3><p>Updated policy.</p>")
    assert unverified_policy_placements(deps) == [33]


def test_author_into_empty_leaf_creates_cell(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    _seed_inventory(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [{
            "sheet": _S12, "row": 50, "html": "<p>grounded prose</p>",
            "note_num": 4, "source_pages": [19], "evidence": "fair value note"}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert 50 in cells and "grounded prose" in cells[50]
    assert deps.writes_performed == 1
    # A snapshot was taken before the write (reversibility).
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is True


def test_author_into_abstract_row_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 51, "ABSTRACT", "Section header")
    _seed_inventory(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [{
            "sheet": _S12, "row": 51, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19]}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 51 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("not_leaf") == 1


def test_author_into_occupied_row_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 49, "LEAF", "Occupied")
    _seed_inventory(db_path, run_id, 4)
    _seed_cell(db_path, run_id, 49, "<p>existing</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [{
            "sheet": _S12, "row": 49, "html": "<p>new</p>", "note_num": 4,
            "source_pages": [19]}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert _cells(db_path, run_id)[49] == "<p>existing</p>"  # untouched
    assert deps.fix_rejections.get("occupied_target") == 1


def test_ungrounded_author_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "X")
    _seed_inventory(db_path, run_id, 4)
    # No view_pdf_pages first → source_pages not in viewed set.
    model = _scripted([
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [{
            "sheet": _S12, "row": 50, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19]}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 50 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("ungrounded") == 1


def test_edit_preserves_leading_heading(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49,
               "<h3>4 Investment property</h3><p>OLD body</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="edit_note_cells", args={"edits": [{
            "sheet": _S12, "row": 49, "html": "<p>NEW body</p>",
            "source_pages": [19]}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    html = _cells(db_path, run_id)[49]
    assert "<h3>4 Investment property</h3>" in html  # heading preserved
    assert "NEW body" in html and "OLD body" not in html


def test_move_reroutes_and_clears_source(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>fair value of FI</p>")
    _seed_node(db_path, 80, "LEAF", "Disclosure of financial instruments")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [22]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": _S12, "from_row": 49, "to_sheet": _S12, "to_row": 80,
            "source_pages": [22]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert 49 not in cells  # source cleared
    assert 80 in cells and "fair value of FI" in cells[80]
    with repo.db_session(db_path) as conn:
        stored_uuid = conn.execute(
            "SELECT concept_uuid FROM notes_cells "
            "WHERE run_id = ? AND sheet = ? AND row = 80",
            (run_id, _S12),
        ).fetchone()[0]
    assert stored_uuid == "n80"


def test_reviewer_can_atomically_correct_the_wrong_first_source_placement(
    db_path: Path,
) -> None:
    from types import SimpleNamespace
    from notes import source_repository as sources, source_write
    from notes.source_models import SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 49, "LEAF", "Income tax")
    _seed_node(db_path, 80, "LEAF", "Deferred tax")
    with repo.db_session(db_path) as conn:
        generation = sources.begin_generation(
            conn, run_id, input_kind="prepared_document"
        )
        sources.write_blocks(conn, generation, [
            SourceBlock(
                block_id="tax-policy",
                block_kind="paragraph",
                reading_order=1,
                canonical_html="<p>Deferred tax is recognised.</p>",
                source_note_id="policy-tax",
                page=22,
            ),
        ])
        sources.write_notes(conn, generation, [
            SourceNote(
                source_note_id="policy-tax",
                top_note_num="13",
                title="Deferred tax",
                block_ids=["tax-policy"],
            ),
        ])
        sources.activate_generation(conn, generation)
        source_write.write_cell_from_blocks(
            conn,
            run_id=run_id,
            generation_id=generation,
            sheet=_S12,
            row=49,
            block_ids=["tax-policy"],
            template_prefix=_PREFIX,
        )
        with pytest.raises(source_write.SourcePlacementConflict) as caught:
            source_write.write_cell_from_blocks(
                conn,
                run_id=run_id,
                generation_id=generation,
                sheet=_S12,
                row=80,
                block_ids=["tax-policy"],
                template_prefix=_PREFIX,
            )
        source_write.record_placement_conflict(
            conn,
            run_id=run_id,
            conflict=caught.value,
            source_pages=[22],
        )

    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    assert len(context["placement_conflicts"]) == 1
    assert "PLACEMENT CONFLICTS" in ra.build_notes_reviewer_packet(context)
    deps.viewed_pages.add(22)
    resolver = next(
        ts.tools["resolve_placement_conflict"].function
        for ts in agent.toolsets
        if "resolve_placement_conflict" in getattr(ts, "tools", {})
    )
    conflict_id = context["placement_conflicts"][0]["packet_finding_id"]

    result = resolver(
        SimpleNamespace(deps=deps),
        conflict_id,
        "move_to_proposed",
        [22],
        "The policy specifically addresses deferred tax on page 22.",
    )

    assert result == "ok: placement conflict resolved with move_to_proposed"
    with repo.db_session(db_path) as conn:
        cells = {
            (cell.sheet, cell.row): cell
            for cell in repo.list_notes_cells_for_run(conn, run_id)
        }
        placements = sources.active_placements(conn, generation)
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert (_S12, 49) not in cells
    assert "Deferred tax is recognised" in cells[(_S12, 80)].html
    assert [(p["block_id"], p["row"]) for p in placements] == [
        ("tax-policy", 80)
    ]
    assert flags[0]["status"] == "answered"


def test_reviewer_cell_move_reconciles_stale_same_block_conflict(db_path: Path):
    from notes import review_move, source_repository as sources, source_write
    from notes.source_models import SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 49, "LEAF", "Income tax")
    _seed_node(db_path, 80, "LEAF", "Deferred tax")
    with repo.db_session(db_path) as conn:
        generation = sources.begin_generation(
            conn, run_id, input_kind="prepared_document")
        sources.write_blocks(conn, generation, [SourceBlock(
            block_id="tax", block_kind="paragraph", reading_order=1,
            canonical_html="<p>Deferred tax is recognised.</p>",
            source_note_id="note-tax", page=22,
        )])
        sources.write_notes(conn, generation, [SourceNote(
            source_note_id="note-tax", top_note_num="13",
            title="Deferred tax", block_ids=["tax"],
        )])
        sources.activate_generation(conn, generation)
        source_write.write_cell_from_blocks(conn, run_id=run_id,
            generation_id=generation, sheet=_S12, row=49,
            block_ids=["tax"], template_prefix=_PREFIX)
        with pytest.raises(source_write.SourcePlacementConflict) as caught:
            source_write.write_cell_from_blocks(conn, run_id=run_id,
                generation_id=generation, sheet=_S12, row=80,
                block_ids=["tax"], template_prefix=_PREFIX)
        source_write.record_placement_conflict(
            conn, run_id=run_id, conflict=caught.value, source_pages=[22])
    with repo.db_session(db_path) as conn:
        assert review_move.reconcile_reviewed_placement_conflicts(
            conn, run_id=run_id) == 0
        revision = conn.execute(
            "SELECT content_revision FROM notes_cells WHERE run_id=? AND sheet=? AND row=49",
            (run_id, _S12),
        ).fetchone()[0]
        review_move.move_note_during_review(conn, run_id=run_id,
            sheet=_S12, row=49, destination_sheet=_S12, destination_row=80,
            expected_revision=revision, destination_revision=None)
        assert review_move.reconcile_reviewed_placement_conflicts(
            conn, run_id=run_id) == 1
        assert review_move.reconcile_reviewed_placement_conflicts(
            conn, run_id=run_id) == 0
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert flags[0]["status"] == "answered"


def _field_conflict(db_path: Path):
    """Two extraction writes of DIFFERENT notes into one field. The second is
    kept as a proposal; the field still holds the first note."""
    from notes import source_repository as sources, source_write
    from notes.source_models import SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 49, "LEAF", "Disclosure of other receivables")
    _seed_node(db_path, 80, "LEAF", "Disclosure of amounts due from related companies")
    _seed_node(db_path, 81, "LEAF", "Disclosure of deposits")
    with repo.db_session(db_path) as conn:
        generation = sources.begin_generation(conn, run_id, input_kind="prepared_document")
        sources.write_blocks(conn, generation, [
            SourceBlock(block_id="b12", block_kind="paragraph", reading_order=1,
                        canonical_html="<p>Deposits are refundable.</p>",
                        source_note_id="note-12", page=22),
            SourceBlock(block_id="b13", block_kind="paragraph", reading_order=2,
                        canonical_html="<p>The related company balance is unsecured.</p>",
                        source_note_id="note-13", page=23),
        ])
        sources.write_notes(conn, generation, [
            SourceNote(source_note_id="note-12", top_note_num="12",
                       title="Other receivables", block_ids=["b12"]),
            SourceNote(source_note_id="note-13", top_note_num="13",
                       title="Amount due from a related company", block_ids=["b13"]),
        ])
        sources.activate_generation(conn, generation)
    with repo.db_session(db_path) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=generation, sheet=_S12, row=49,
            block_ids=["b12"], template_prefix=_PREFIX,
        )
    with repo.db_session(db_path) as conn:
        with pytest.raises(source_write.SourcePlacementConflict) as caught:
            source_write.write_cell_from_blocks(
                conn, run_id=run_id, generation_id=generation, sheet=_S12, row=49,
                block_ids=["b13"], template_prefix=_PREFIX,
            )
    with repo.db_session(db_path) as conn:
        source_write.record_placement_conflict(
            conn, run_id=run_id, conflict=caught.value, source_pages=[22, 23],
        )
    return run_id, generation, caught.value


def test_second_note_for_a_field_becomes_a_proposal_not_an_overwrite(db_path: Path):
    """Extraction order must not decide a field. The later write of a
    different note leaves the field unchanged and records both notes for the
    reviewer; rewriting the same note is still an ordinary write."""
    from notes import source_repository as sources, source_write

    run_id, generation, conflict = _field_conflict(db_path)

    assert conflict.match_kind == "same_field"
    assert "12 Other receivables" in conflict.existing_notes
    assert "13 Amount due from a related company" in conflict.source_notes
    with repo.db_session(db_path) as conn:
        placements = sources.active_placements(conn, generation)
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=generation, sheet=_S12, row=49,
            block_ids=["b12"], template_prefix=_PREFIX,
        )
    assert [(p["block_id"], p["row"]) for p in placements] == [("b12", 49)]


@pytest.mark.parametrize("decision, other_row, expected", [
    ("use_proposed", 80, [("b12", 80), ("b13", 49)]),
    ("keep_existing", 80, [("b12", 49), ("b13", 80)]),
])
def test_reviewer_decides_a_field_conflict(db_path: Path, decision, other_row, expected):
    from types import SimpleNamespace
    from notes import source_repository as sources

    run_id, generation, _ = _field_conflict(db_path)
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    packet = ra.build_notes_reviewer_packet(context)
    assert packet.index("PLACEMENT CONFLICTS") < packet.index("SOURCE COMPLETENESS")
    assert "FIELD CONFLICT" in packet
    deps.viewed_pages.update({22, 23})
    resolver = next(
        ts.tools["resolve_placement_conflict"].function
        for ts in agent.toolsets
        if "resolve_placement_conflict" in getattr(ts, "tools", {})
    )
    result = resolver(
        SimpleNamespace(deps=deps),
        context["placement_conflicts"][0]["ref"],
        decision, [22, 23], "Grounded on pages 22 and 23.",
        other_row=other_row,
    )

    assert result.startswith(f"ok: field conflict resolved with {decision}")
    with repo.db_session(db_path) as conn:
        placements = sources.active_placements(conn, generation)
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert sorted((p["block_id"], p["row"]) for p in placements) == expected
    assert flags[0]["status"] == "answered"


def test_field_conflict_cannot_be_answered_while_stranding_other_note(db_path: Path):
    from notes import review_move, source_repository as sources

    run_id, generation, _ = _field_conflict(db_path)
    with repo.db_session(db_path) as conn:
        flag = repo.fetch_notes_review_flags(conn, run_id)[0]
        with pytest.raises(review_move.MoveConflict, match="Choose other_row"):
            review_move.resolve_field_collision(
                conn, run_id=run_id, flag_id=flag["id"],
                finding_id=flag["finding_id"], decision="keep_existing",
                answer="Keep Note 12", template_prefix=_PREFIX,
            )
        placements = sources.active_placements(conn, generation)
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert [(p["block_id"], p["row"]) for p in placements] == [("b12", 49)]
    assert flags[0]["status"] == "open"


def test_refused_conflict_can_be_escalated_by_its_short_reference(db_path: Path):
    from types import SimpleNamespace

    run_id, _, _ = _field_conflict(db_path)
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    deps.viewed_pages.update({22, 23})
    tools = {name: tool.function for ts in agent.toolsets
             for name, tool in getattr(ts, "tools", {}).items()}
    ctx = SimpleNamespace(deps=deps)
    rejected = tools["resolve_placement_conflict"](
        ctx, "C1", "keep_existing", [22, 23], "Cannot choose another field.")
    assert rejected.startswith("rejected:")
    assert "C1" in rejected and "raise_flag" in rejected
    accepted = tools["raise_flag"](
        ctx, "needs_human", "No supported alternative field", finding_id="C1",
        source_pages=[22, 23], evidence="Both notes need this specific field.")
    assert "sent to human review" in accepted
    verified = tools["verify_findings"](ctx)
    assert "sent to human review" in verified and "VERIFIED" not in verified
    assert deps.flags[0]["finding_id"] == context["placement_conflicts"][0]["packet_finding_id"]


def test_conflict_references_survive_resolution_of_another_conflict(db_path):
    from types import SimpleNamespace

    run_id, _, _ = _placement_conflict_reviewer(db_path)
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    deps.viewed_pages.add(22)
    tools = {name: tool.function for ts in agent.toolsets
             for name, tool in getattr(ts, "tools", {}).items()}
    ctx = SimpleNamespace(deps=deps)
    first, second = context["placement_conflicts"]
    result = tools["resolve_placement_conflict"](
        ctx, first["ref"], "keep_existing", [22], "The first field is correct.")
    assert result.startswith("ok:")
    pending = tools["verify_findings"](ctx)
    assert f"still open: {second['ref']}" in pending
    assert tools["raise_flag"](
        ctx, "needs_human", "Other destination needs judgment", finding_id=second["ref"],
        source_pages=[22], evidence="Two possible fields for the source disclosure.",
    ).startswith("flagged and sent to human review")
    assert deps.flags[-1]["finding_id"] == second["packet_finding_id"]


@pytest.mark.parametrize("block_ids, prefix, recorded", [
    ([], "rejected:", 0), (["b12"], "partial:", 0),
    (["b12", "b13"], "partial:", 1), (["b13"], "ok:", 1),
])
def test_disposition_batch_reports_actual_work(db_path, block_ids, prefix, recorded):
    from types import SimpleNamespace

    run_id, generation, _ = _field_conflict(db_path)
    agent, deps, _ = _agent(db_path, run_id, _scripted([]))
    tool = next(ts.tools["record_block_dispositions"].function for ts in agent.toolsets
                if "record_block_dispositions" in getattr(ts, "tools", {}))
    result = tool(SimpleNamespace(deps=deps), block_ids, "routed")
    assert result.startswith(prefix)
    assert "retry" not in result
    with repo.db_session(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM notes_disposition_events WHERE generation_id=? "
            "AND actor='notes_reviewer'", (generation,),
        ).fetchone()[0]
    assert count == recorded


def test_distinct_notes_cannot_combine_in_specific_field(db_path: Path):
    from notes import review_move

    run_id, _generation, _conflict = _field_conflict(db_path)
    with repo.db_session(db_path) as conn:
        flag = repo.fetch_notes_review_flags(conn, run_id)[0]
        with pytest.raises(review_move.MoveConflict, match="catch-all"):
            review_move.resolve_field_collision(
                conn, run_id=run_id, flag_id=flag["id"],
                finding_id=flag["finding_id"], decision="combine",
                answer="Combine", template_prefix=_PREFIX,
            )


def test_field_conflict_never_overwrites_an_occupied_destination(db_path: Path):
    from types import SimpleNamespace
    from notes import source_repository as sources

    run_id, generation, _ = _field_conflict(db_path)
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S12, row=81,
            label="Disclosure of deposits", html="<p>Authored.</p>",
            evidence=None, source_pages=[],
        )
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    deps.viewed_pages.update({22, 23})
    resolver = next(
        ts.tools["resolve_placement_conflict"].function
        for ts in agent.toolsets
        if "resolve_placement_conflict" in getattr(ts, "tools", {})
    )
    # The model's copy of the long id often differs only in escaping.
    mangled = context["placement_conflicts"][0]["packet_finding_id"].replace("\\", "\\\\")
    result = resolver(
        SimpleNamespace(deps=deps), mangled,
        "use_proposed", [22, 23], "Grounded.", other_row=81,
    )

    assert result.startswith("rejected:") and "already holds content" in result
    with repo.db_session(db_path) as conn:
        placements = sources.active_placements(conn, generation)
    assert [(p["block_id"], p["row"]) for p in placements] == [("b12", 49)]


def _placement_conflict_reviewer(db_path, *, existing_ids=("tax",), proposed_ids=("tax",)):
    from types import SimpleNamespace
    from notes import source_repository as sources, source_write
    from notes.source_models import SourceBlock

    run_id = _seed_run(db_path)
    for row in (49, 80, 81):
        _seed_node(db_path, row, "LEAF", f"Policy {row}")
    with repo.db_session(db_path) as conn:
        generation = sources.begin_generation(conn, run_id, input_kind="prepared_document")
        sources.write_blocks(conn, generation, [
            SourceBlock("tax", "paragraph", 1, "<p>Deferred tax policy.</p>"),
            SourceBlock("other", "paragraph", 2, "<p>Other policy.</p>"),
            SourceBlock("copy", "paragraph", 3, "<p>Deferred tax policy.</p>"),
        ])
        sources.activate_generation(conn, generation)
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=generation, sheet=_S12, row=49,
            block_ids=existing_ids, template_prefix=_PREFIX,
        )
        for row in (80, 81):
            with pytest.raises(source_write.SourcePlacementConflict) as caught:
                source_write.write_cell_from_blocks(
                    conn, run_id=run_id, generation_id=generation, sheet=_S12, row=row,
                    block_ids=proposed_ids, template_prefix=_PREFIX,
                )
            source_write.record_placement_conflict(
                conn, run_id=run_id, conflict=caught.value, source_pages=[22],
            )
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    deps.viewed_pages.add(22)
    tool = next(ts.tools["resolve_placement_conflict"].function for ts in agent.toolsets
                if "resolve_placement_conflict" in getattr(ts, "tools", {}))
    conflicts = sorted(context["placement_conflicts"], key=lambda c: c["target"]["row"])

    def resolve(index=0, decision="keep_existing"):
        return tool(SimpleNamespace(deps=deps), conflicts[index]["packet_finding_id"],
                    decision, [22], "The PDF supports this destination.")
    return run_id, generation, resolve


def test_reviewer_can_confirm_the_first_source_placement(db_path: Path) -> None:
    run_id, _, resolve = _placement_conflict_reviewer(db_path)
    assert resolve() == "ok: placement conflict resolved with keep_existing"
    assert 49 in _cells(db_path, run_id)
    assert 80 not in _cells(db_path, run_id)
    with repo.db_session(db_path) as conn:
        assert sum(f["status"] == "answered" for f in repo.fetch_notes_review_flags(conn, run_id)) == 1


@pytest.mark.parametrize("existing_ids,proposed_ids", [
    (("tax", "other"), ("tax",)),
    (("tax",), ("tax", "other")),
])
def test_partial_placement_move_preserves_both_cells_and_open_flag(db_path, existing_ids, proposed_ids):
    run_id, _, resolve = _placement_conflict_reviewer(
        db_path, existing_ids=existing_ids, proposed_ids=proposed_ids,
    )
    before = _cells(db_path, run_id)
    assert resolve(decision="move_to_proposed").startswith("rejected:")
    assert _cells(db_path, run_id) == before
    with repo.db_session(db_path) as conn:
        assert all(f["status"] == "open" for f in repo.fetch_notes_review_flags(conn, run_id))


def test_second_conflict_cannot_confirm_a_destination_already_moved(db_path):
    run_id, _, resolve = _placement_conflict_reviewer(db_path)
    assert resolve(decision="move_to_proposed").startswith("ok:")
    assert resolve(1).startswith("rejected: The source placement changed")
    assert set(_cells(db_path, run_id)) == {80}
    with repo.db_session(db_path) as conn:
        assert sorted(f["status"] for f in repo.fetch_notes_review_flags(conn, run_id)) == ["answered", "open"]


@pytest.mark.parametrize("change", ["revision", "generation", "placements", "answered", "legacy"])
@pytest.mark.parametrize("decision", ["keep_existing", "move_to_proposed"])
def test_conflict_resolution_revalidates_live_state(db_path, change, decision):
    from notes import source_repository as sources
    run_id, generation, resolve = _placement_conflict_reviewer(db_path)
    with repo.db_session(db_path) as conn:
        if change == "revision":
            repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                                   label="Changed", html="<p>Human correction.</p>")
        elif change == "generation":
            from notes.source_models import SourceBlock
            newer = sources.begin_generation(conn, run_id, input_kind="prepared_document")
            sources.write_blocks(conn, newer, [SourceBlock("new", "paragraph", 1, "<p>New source.</p>")])
            sources.activate_generation(conn, newer)
        elif change == "placements":
            sources.set_cell_placements(conn, run_id, generation, _S12, 49, [])
        elif change == "legacy":
            import json
            for flag in repo.fetch_notes_review_flags(conn, run_id):
                evidence = json.loads(flag["evidence"])
                evidence["existing"][0].pop("content_revision")
                conn.execute("UPDATE notes_review_flags SET evidence=? WHERE id=?",
                             (json.dumps(evidence), flag["id"]))
        else:
            conn.execute("UPDATE notes_review_flags SET status='answered' WHERE run_id=?", (run_id,))
    before = _cells(db_path, run_id)
    assert resolve(decision=decision).startswith("rejected:")
    assert _cells(db_path, run_id) == before


def test_same_render_conflict_can_move_the_unchanged_complete_cell(db_path):
    run_id, _, resolve = _placement_conflict_reviewer(db_path, proposed_ids=("copy",))
    assert resolve(decision="move_to_proposed").startswith("ok:")
    assert set(_cells(db_path, run_id)) == {80}


def test_flag_answer_failure_rolls_back_the_placement_move(db_path, monkeypatch):
    from notes import source_repository as sources
    run_id, generation, resolve = _placement_conflict_reviewer(db_path)
    before = _cells(db_path, run_id)
    monkeypatch.setattr(repo, "answer_notes_review_flag", lambda *a, **kw: False)
    assert resolve(decision="move_to_proposed").startswith("rejected:")
    assert _cells(db_path, run_id) == before
    with repo.db_session(db_path) as conn:
        assert {p["row"] for p in sources.active_placements(conn, generation)} == {49}
        assert all(f["status"] == "open" for f in repo.fetch_notes_review_flags(conn, run_id))


def test_edit_with_script_only_html_is_refused_not_destructive(db_path: Path) -> None:
    """Peer-review #1: content that sanitises to empty must NOT overwrite the
    existing valid body — the edit is refused before any write."""
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<h3>4 X</h3><p>VALID body</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="edit_note_cells", args={"edits": [{
            "sheet": _S12, "row": 49, "html": "<script>alert(1)</script>",
            "source_pages": [19]}]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    # Original body intact; the destructive write was refused.
    assert "VALID body" in _cells(db_path, run_id)[49]
    assert deps.fix_rejections.get("empty_content") == 1
    assert deps.writes_performed == 0


def test_move_from_non_prose_sheet_refused(db_path: Path) -> None:
    """Peer-review #2: both ends of a move must be prose sheets."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 80, "LEAF", "X")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": "SOFP", "from_row": 10, "to_sheet": _S12, "to_row": 80,
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.writes_performed == 0
    assert 80 not in _cells(db_path, run_id)


def test_title_detector_flags_missing_heading():
    issues = ra.detect_title_format_issues([
        {"sheet": _S12, "row": 49, "label": "X", "html": "<p>no heading</p>"},
        {"sheet": _S12, "row": 50, "label": "Y",
         "html": "<h3>5 Revenue</h3><p>ok</p>"},
    ])
    assert [i["row"] for i in issues] == [49]


def test_title_detector_preserves_source_heading_hierarchy_only_for_source_cells():
    issues = ra.detect_title_format_issues([
        {"sheet": _S12, "row": 49, "label": "source h2",
         "html": "<h2>5 Revenue</h2><p>ok</p>", "source_built": True},
        {"sheet": _S12, "row": 50, "label": "authored h2",
         "html": "<h2>5 Revenue</h2><p>wrong contract</p>"},
        {"sheet": _S12, "row": 51, "label": "source missing heading",
         "html": "<p>still malformed</p>", "source_built": True},
    ])
    assert [i["row"] for i in issues] == [50]


def test_duplicate_detector_accepts_distinct_source_partitions():
    entries = [
        {"sheet": "Notes-SummaryofAccPol", "row": 42,
         "source_note_refs": ["4"]},
        {"sheet": "Notes-Listofnotes", "row": 87,
         "source_note_refs": ["4"]},
    ]
    distinct = {
        ("Notes-SummaryofAccPol", 42): {"policy-prose"},
        ("Notes-Listofnotes", 87): {"disclosure-table"},
    }
    assert det.detect_cross_sheet_duplicates_by_ref(
        entries, substantive_blocks_by_cell=distinct,
    ) == []
    overlapping = {**distinct, ("Notes-Listofnotes", 87): {"policy-prose"}}
    assert len(det.detect_cross_sheet_duplicates_by_ref(
        entries, substantive_blocks_by_cell=overlapping,
    )) == 1


def test_packet_renders_present_families_only():
    packet = ra.build_notes_reviewer_packet({
        "row_collisions": [{"row": 49, "row_label": "FV", "note_nums": [4, 20],
                            "source_note_refs": ["4.1", "20.7"]}],
    })
    assert "SAME-SHEET COLLISION" in packet
    assert "CROSS-SHEET DUPLICATION" not in packet  # no dup findings supplied


def test_packet_clean_run_is_short():
    packet = ra.build_notes_reviewer_packet({})
    assert "No structural findings" in packet


def test_read_cells_batch_helper_single_query(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>alpha</p>")
    _seed_cell(db_path, run_id, 51, "<p>gamma</p>")
    found = ra._read_cells(str(db_path), run_id, _S12, [49, 50, 51])
    # 49 and 51 exist; 50 was never seeded so it's absent (not null-keyed here).
    assert set(found) == {49, 51}
    assert "alpha" in found[49]["html"] and "gamma" in found[51]["html"]
    assert ra._read_cells(str(db_path), run_id, _S12, []) == {}


def _tool_returns(result, tool_name: str) -> list[str]:
    from pydantic_ai.messages import ToolReturnPart
    return [
        p.content for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolReturnPart) and p.tool_name == tool_name
    ]


def test_read_note_cells_tool_returns_all_rows_in_one_call(db_path: Path) -> None:
    import json as _json
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>alpha</p>")
    _seed_cell(db_path, run_id, 51, "<p>gamma</p>")
    model = _scripted([
        [ToolCallPart(tool_name="read_note_cells",
                      args={"sheet": _S12, "rows": [49, 50, 51]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    result = agent.run_sync("go", deps=deps)
    returns = _tool_returns(result, "read_note_cells")
    assert len(returns) == 1  # one round-trip covered all three rows
    payload = _json.loads(returns[0])
    assert set(payload) == {"49", "50", "51"}
    assert "alpha" in payload["49"]["html"]
    assert "gamma" in payload["51"]["html"]
    assert payload["50"] is None  # empty row reported as null, not omitted


def test_read_note_cells_serves_a_single_row(db_path: Path) -> None:
    # The plural tool is the ONLY read tool — a single cell is rows=[49].
    import json as _json
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>alpha</p>")
    model = _scripted([
        [ToolCallPart(tool_name="read_note_cells", args={"sheet": _S12, "rows": [49]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    result = agent.run_sync("go", deps=deps)
    payload = _json.loads(_tool_returns(result, "read_note_cells")[0])
    assert set(payload) == {"49"} and "alpha" in payload["49"]["html"]


def test_read_note_cells_rejects_over_cap(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    over = list(range(1, ra.READ_CELLS_MAX_ROWS + 2))  # cap + 1 distinct rows
    model = _scripted([
        [ToolCallPart(tool_name="read_note_cells", args={"sheet": _S12, "rows": over})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    result = agent.run_sync("go", deps=deps)
    msg = _tool_returns(result, "read_note_cells")[0]
    assert "Too many rows" in msg and str(len(over)) in msg


def test_read_note_cells_dedups_so_repeats_dont_eat_the_cap(db_path: Path) -> None:
    # cap+1 entries but only 2 distinct rows → allowed, not rejected.
    import json as _json
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>alpha</p>")
    rows = [49, 51] * ra.READ_CELLS_MAX_ROWS
    model = _scripted([
        [ToolCallPart(tool_name="read_note_cells", args={"sheet": _S12, "rows": rows})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    result = agent.run_sync("go", deps=deps)
    payload = _json.loads(_tool_returns(result, "read_note_cells")[0])
    assert set(payload) == {"49", "51"}


# --------------------------------------------------------------------------
# Batched prose writes — author_note_cells / edit_note_cells take a LIST and
# apply each item independently (one rejected item never blocks the others).
# --------------------------------------------------------------------------


def test_author_note_cells_batch_applies_each_item_independently(db_path: Path) -> None:
    """One batch authoring two cells: a LEAF lands, an ABSTRACT row is refused;
    exactly one write happens and the per-item guard tally records the reject."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    _seed_node(db_path, 51, "ABSTRACT", "Section header")
    _seed_inventory(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [
            {"sheet": _S12, "row": 50, "html": "<p>grounded prose</p>",
             "note_num": 4, "source_pages": [19], "evidence": "note X"},
            {"sheet": _S12, "row": 51, "html": "<p>x</p>",
             "note_num": 4, "source_pages": [19]},
        ]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert 50 in cells and "grounded prose" in cells[50]  # leaf landed
    assert 51 not in cells                                # abstract refused
    assert deps.writes_performed == 1
    assert deps.fix_rejections.get("not_leaf") == 1


def test_edit_note_cells_batch_edits_multiple_bodies(db_path: Path) -> None:
    """A two-item edit batch updates both bodies in one call, each preserving
    its own leading heading."""
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<h3>4 Investment property</h3><p>OLD a</p>")
    _seed_cell(db_path, run_id, 50, "<h3>5 Inventories</h3><p>OLD b</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19, 20]})],
        [ToolCallPart(tool_name="edit_note_cells", args={"edits": [
            {"sheet": _S12, "row": 49, "html": "<p>NEW a</p>", "source_pages": [19]},
            {"sheet": _S12, "row": 50, "html": "<p>NEW b</p>", "source_pages": [20]},
        ]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert "<h3>4 Investment property</h3>" in cells[49] and "NEW a" in cells[49]
    assert "<h3>5 Inventories</h3>" in cells[50] and "NEW b" in cells[50]
    assert "OLD a" not in cells[49] and "OLD b" not in cells[50]
    assert deps.writes_performed == 2


def test_author_note_cells_rejects_empty_list(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    model = _scripted([
        [ToolCallPart(tool_name="author_note_cells", args={"authored": []})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.writes_performed == 0


def test_author_note_cells_isolates_unexpected_error_per_item(db_path, monkeypatch):
    """An UNEXPECTED exception on one item must not abort the sibling or lose
    the report: the good cell still lands, the failing one becomes a rejected
    line, and the batch tool returns normally (doesn't raise)."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Good")
    _seed_node(db_path, 60, "LEAF", "Boom")
    _seed_inventory(db_path, run_id, 4)

    real_upsert = repo.upsert_notes_cell

    def flaky_upsert(conn, *, run_id, sheet, row, label, html, evidence=None,
                     source_pages=None, **kw):
        if row == 60:
            raise RuntimeError("simulated DB failure")
        return real_upsert(conn, run_id=run_id, sheet=sheet, row=row,
                           label=label, html=html, evidence=evidence,
                           source_pages=source_pages, **kw)

    monkeypatch.setattr(repo, "upsert_notes_cell", flaky_upsert)

    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cells", args={"authored": [
            {"sheet": _S12, "row": 50, "html": "<p>good prose</p>",
             "note_num": 4, "source_pages": [19]},
            {"sheet": _S12, "row": 60, "html": "<p>boom prose</p>",
             "note_num": 4, "source_pages": [19]},
        ]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)  # must not raise

    cells = _cells(db_path, run_id)
    assert 50 in cells and "good prose" in cells[50]  # sibling still landed
    assert 60 not in cells                            # failing item didn't write
    assert deps.writes_performed == 1


def test_prepared_missing_unnumbered_source_triggers_review_and_can_be_relinked(db_path):
    from types import SimpleNamespace
    from notes import source_repository as srepo
    from notes.source_models import OwnerKind, SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    with repo.db_session(db_path) as conn:
        gen = srepo.begin_generation(conn, run_id, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("u1", "paragraph", 1,
            "<p>Unnumbered disclosure.</p>", source_note_id="unumbered-disclosure", owner_kind=OwnerKind.NOTE)])
        srepo.write_notes(conn, gen, [SourceNote("unumbered-disclosure", "", "Disclosure", ["u1"])])
        srepo.activate_generation(conn, gen)
    agent, deps, context = _agent(db_path, run_id, _scripted([]))
    assert context["source_integrity_findings"]
    assert ra.count_open_items(context) > 0
    assert "SOURCE COMPLETENESS" in ra.build_notes_reviewer_packet(context)
    funcs = {name: tool.function for ts in agent.toolsets for name, tool in getattr(ts, "tools", {}).items()}
    assert {"list_source_notes", "read_source_manifest", "view_source_blocks", "list_source_destinations"} <= funcs.keys()
    ctx = SimpleNamespace(deps=deps)
    assert "unumbered-disclosure" in funcs["list_source_notes"](ctx)
    assert "u1" in funcs["read_source_manifest"](ctx, "unumbered-disclosure")
    assert "Unnumbered disclosure" in funcs["view_source_blocks"](ctx, ["u1"])
    for name, args in [("list_source_notes", ()),
                       ("read_source_manifest", ("unumbered-disclosure",)),
                       ("view_source_blocks", (["u1"],))]:
        full = funcs[name](ctx, *args)
        tail = funcs[name](ctx, *args, offset=5)
        body = lambda text: text.split("<<<SOURCE>>>\n", 1)[1].split("\n<<<END_SOURCE>>>", 1)[0]
        assert body(tail) == body(full)[5:]
    result = funcs["relink_note_cell"](ctx, sheet=_S12, row=50, block_ids=["u1"])
    assert result.startswith("ok:")
    rejected = funcs["record_block_dispositions"](ctx, ["u1"], "structured_consumed")
    assert "already placed" in rejected
    with repo.db_session(db_path) as conn:
        usage = conn.execute(
            "SELECT sheet,row,disposition FROM notes_block_usages "
            "WHERE generation_id=? AND block_id='u1'", (gen,),
        ).fetchone()
    assert (usage["sheet"], usage["row"], usage["disposition"]) == (
        _S12, 50, "included")
    refreshed = ra.recompute_notes_findings(deps)
    assert refreshed["source_integrity_findings"] == []
    assert ra.finding_keys(context) - ra.finding_keys(refreshed)


def test_reviewer_transfers_one_complete_section_from_mixed_cell(db_path, monkeypatch):
    """Moving standards text must retain the basis section in its source cell."""
    from notes import source_repository as sources, source_write, review_move
    from notes.source_models import SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 57, "LEAF", "Disclosure of basis of preparation")
    _seed_node(db_path, 7, "LEAF", "Disclosure of new standards")
    with repo.db_session(db_path) as conn:
        gen = sources.begin_generation(conn, run_id, input_kind="prepared_document")
        blocks = [
            SourceBlock("root", "heading", 0, "<h2>2. Policies</h2>",
                        source_note_id="note-2", page=16),
            SourceBlock("h1", "heading", 1, "<h3>2.1 Basis</h3>",
                        source_note_id="note-2", page=16),
            SourceBlock("p1", "paragraph", 2, "<p>Historical cost basis.</p>",
                        source_note_id="note-2", page=16),
            SourceBlock("h2", "heading", 3, "<h3>2.2 New standards</h3>",
                        source_note_id="note-2", page=17),
            SourceBlock("p2", "paragraph", 4, "<p>New standards are not yet effective.</p>",
                        source_note_id="note-2", page=17),
        ]
        sources.write_blocks(conn, gen, blocks)
        sources.write_notes(conn, gen, [SourceNote("note-2", "2", "Policies",
            [block.block_id for block in blocks])])
        sources.activate_generation(conn, gen)
        source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
            sheet=_S12, row=57, block_ids=[block.block_id for block in blocks],
            template_prefix=_PREFIX)
    real_write = source_write.write_cell_from_blocks
    calls = 0

    def fail_destination(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("destination write failed")
        return real_write(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(source_write, "write_cell_from_blocks", fail_destination)
        with pytest.raises(RuntimeError, match="destination write failed"):
            with repo.db_session(db_path) as conn:
                review_move.transfer_source_sections(conn, run_id=run_id,
                    generation_id=gen, source_sheet=_S12, source_row=57,
                    destination_sheet=_S12, destination_row=7,
                    section_ids=["section:note-2:2.2"], template_prefix=_PREFIX)
    unchanged = _cells(db_path, run_id)
    assert "not yet effective" in unchanged[57]
    assert 7 not in unchanged
    with repo.db_session(db_path) as conn:
        review_move.transfer_source_sections(conn, run_id=run_id, generation_id=gen,
            source_sheet=_S12, source_row=57, destination_sheet=_S12,
            destination_row=7, section_ids=["section:note-2:2.2"],
            template_prefix=_PREFIX)
    cells = _cells(db_path, run_id)
    assert "Historical cost basis" in cells[57]
    assert "not yet effective" not in cells[57]
    assert "not yet effective" in cells[7]
    with repo.db_session(db_path) as conn:
        placed = {(p["block_id"], p["row"]) for p in sources.active_placements(conn, gen)}
    assert ("p1", 57) in placed and ("p2", 7) in placed


@pytest.mark.parametrize("verdict", [None, "correct", "needs_human"])
def test_complete_prepared_sections_are_reviewed_as_partition_not_split(db_path, tmp_path, verdict):
    from notes import source_repository as sources, source_write
    from notes.source_models import SourceBlock, SourceNote

    run_id = _seed_run(db_path)
    _seed_node(db_path, 7, "LEAF", "Effective new standards")
    _seed_node(db_path, 137, "LEAF", "Standards not yet effective")
    with repo.db_session(db_path) as conn:
        gen = sources.begin_generation(conn, run_id, input_kind="prepared_document")
        blocks = [
            SourceBlock("root", "heading", 0, "<h2>2. Policies</h2>",
                        source_note_id="note-2", page=16),
            SourceBlock("h2", "heading", 1, "<h3>2.2 Effective standards</h3>",
                        source_note_id="note-2", page=17),
            SourceBlock("p2", "paragraph", 2, "<p>Now effective.</p>",
                        source_note_id="note-2", page=17),
            SourceBlock("h3", "heading", 3, "<h3>2.3 Future standards</h3>",
                        source_note_id="note-2", page=18),
            SourceBlock("p3", "paragraph", 4, "<p>Not yet effective.</p>",
                        source_note_id="note-2", page=18),
        ]
        sources.write_blocks(conn, gen, blocks)
        sources.write_notes(conn, gen, [SourceNote(
            "note-2", "2", "Policies", [block.block_id for block in blocks],
        )])
        sources.activate_generation(conn, gen)
        for row, section in ((7, "2.2"), (137, "2.3")):
            source_write.write_cell_from_blocks(conn, run_id=run_id,
                generation_id=gen, sheet=_S12, row=row,
                block_ids=[f"section:note-2:{section}"],
                template_prefix=_PREFIX)
        split = {"note_num": 2, "sheet": _S12, "rows": [
            {"row": 7, "row_label": "Effective new standards"},
            {"row": 137, "row_label": "Standards not yet effective"},
        ], "source_note_refs": ["2"]}
        unresolved, partitions = ra.classify_source_section_partitions(
            conn, run_id, [split],
        )
        assert unresolved == [] and partitions == [split]
        incomplete = {**split, "rows": [split["rows"][0],
                                       {"row": 138, "row_label": "Empty"}]}
        unresolved, partitions = ra.classify_source_section_partitions(
            conn, run_id, [incomplete],
        )
        assert unresolved == [incomplete] and partitions == []

    # Complete coverage alone must not turn destination review green.
    import asyncio
    import server
    from types import SimpleNamespace

    verifications = [
        {"row": row, "target_label": label, "verdict": verdict,
         "reason": "Compared the section heading with the field.", "source_pages": [page]}
        for row, label, page in ((7, "Effective new standards", 17),
                                 (137, "Standards not yet effective", 18))
    ]
    steps = [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [17, 18]})],
        [ToolCallPart(tool_name="verify_section_placements", args={"verifications": verifications})],
    ] if verdict else []
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs", model=_scripted(steps),
        output_dir=str(tmp_path), merged_workbook_path=None, sidecar_paths=[], event_queue=None,
    ))
    assert outcome["invoked"]
    assert outcome["error"] == (None if verdict else "notes_reviewer_section_placements_unverified")
    assert outcome["flags_raised"] == (2 if verdict == "needs_human" else 0)

    if verdict == "correct":
        agent, deps, context = _agent(db_path, run_id, _scripted([]))
        assert len(context["section_placements"]) == 2
        assert "STILL open" in ra.format_notes_verification(context, ra.finding_keys(context))
        funcs = {name: tool.function for ts in agent.toolsets
                 for name, tool in getattr(ts, "tools", {}).items()}
        items = [ra.PlacementVerificationItem(**item) for item in verifications]
        ctx = SimpleNamespace(deps=deps)
        assert "rejected" in funcs["verify_section_placements"](ctx, items)
        assert sorted(ra.unverified_section_placements(deps)) == [7, 137]
        deps.viewed_pages.update([17, 18])
        assert "rejected" not in funcs["verify_section_placements"](ctx, items)
        assert ra.unverified_section_placements(deps) == []
        assert not any(key[0] == "section_placement"
                       for key in ra.finding_keys(ra.recompute_notes_findings(deps)))
        with repo.db_session(db_path) as conn:
            repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=7,
                label="Effective new standards", html="<p>Changed standards disclosure.</p>")
        assert ra.unverified_section_placements(deps) == [7]


def test_source_destinations_returns_complete_json_for_long_labels(db_path):
    import json
    from types import SimpleNamespace
    from notes import source_repository as srepo
    from notes.source_models import SourceBlock, OwnerKind

    run_id = _seed_run(db_path)
    with repo.db_session(db_path) as conn:
        gen = srepo.begin_generation(conn, run_id, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("b1", "paragraph", 0,
            "<p>Source disclosure.</p>", source_note_id="n1", owner_kind=OwnerKind.NOTE)])
        srepo.activate_generation(conn, gen)
    for row in range(1, 91):
        _seed_node(db_path, row, "LEAF", f"Disclosure {row}: " + "Long label " * 50)
    agent, deps, _ = _agent(db_path, run_id, _scripted([]))
    funcs = {name: tool.function for ts in agent.toolsets for name, tool in getattr(ts, "tools", {}).items()}
    output = funcs["list_source_destinations"](SimpleNamespace(deps=deps), _S12)
    assert len(json.loads(output)) == 90
