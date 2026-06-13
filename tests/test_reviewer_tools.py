"""Phase 3 — reviewer-agent tool helpers (Steps 5-7).

Pins the standalone, agent-free helpers in
``correction/reviewer_agent.py``:

* ``trace_cascade_source`` — walk down a face cell to the total + children.
* ``apply_reviewer_fix`` + ``evaluate_apply_fix_guard`` — the guarded write.
* ``raise_reviewer_flag`` — write a reviewer flag.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from concept_model.facts_api import FactWrite, write_fact
from correction.reviewer_agent import (
    trace_cascade_source,
    apply_reviewer_fix,
    evaluate_apply_fix_guard,
    raise_reviewer_flag,
    list_run_facts,
    _repeated_values,
    _format_fact_listing,
)


_TEMPLATE = "mfrs-company-sofp-test-v1"
# A sub-sheet total (the formula owner) aliased to a face row, over 2 leaves.
SUBTOTAL = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"
LEAF2 = "00000000-0000-0000-0000-0000000000b2"
ABSTRACT = "00000000-0000-0000-0000-0000000000c0"
OTHER = "00000000-0000-0000-0000-0000000000d0"


def _seed(tmp_path):
    db = tmp_path / "rev.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-29T00:00:00Z', 'x.pdf', 'completed')"
        ).lastrowid)
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES (?, 'x.xlsx', 'linear')", (_TEMPLATE,),
        )
        # Subtotal lives on the sub-sheet; face row aliases it.
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, ?, 'COMPUTED', '*Total PPE', 'SOFP-Sub-CuNonCu', 39, 'B')",
            (SUBTOTAL, _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO concept_render_aliases(concept_uuid, alias_sheet, "
            "alias_row, alias_col) VALUES (?, 'SOFP-CuNonCu', 5, 'B')",
            (SUBTOTAL,),
        )
        for uid, label, row in [
            (LEAF1, "Freehold land", 36),
            (LEAF2, "Buildings", 37),
        ]:
            conn.execute(
                "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
                "canonical_label, render_sheet, render_row, render_col) VALUES "
                "(?, ?, 'LEAF', ?, 'SOFP-Sub-CuNonCu', ?, 'B')",
                (uid, _TEMPLATE, label, row),
            )
            conn.execute(
                "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient)"
                " VALUES (?, ?, 1.0)", (SUBTOTAL, uid),
            )
        # An abstract header + a catch-all "Other" leaf for the guard tests.
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, ?, 'ABSTRACT', 'Non-current assets', 'SOFP-Sub-CuNonCu', 35, 'A')",
            (ABSTRACT, _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, ?, 'LEAF', 'Other property, plant and equipment', "
            "'SOFP-Sub-CuNonCu', 38, 'B')",
            (OTHER, _TEMPLATE),
        )
        conn.commit()
    finally:
        conn.close()
    return db, run_id


def _wf(db, run_id, uid, value, **kw):
    write_fact(db, run_id, FactWrite(
        concept_uuid=uid, period="CY", entity_scope="Company",
        value=value, value_status="observed",
        source=kw.get("source", "extraction"),
        evidence=kw.get("evidence"), actor=kw.get("actor", "agent"),
    ))


# ---------------------------------------------------------------------------
# Step 5 — trace_cascade_source
# ---------------------------------------------------------------------------


def test_trace_by_concept_uuid_returns_children(tmp_path):
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    _wf(db, run_id, LEAF2, 20.0)
    trace = trace_cascade_source(db, run_id, concept_uuid=SUBTOTAL)
    assert trace["found"] is True
    assert trace["concept"]["concept_uuid"] == SUBTOTAL
    assert {c["concept_uuid"] for c in trace["children"]} == {LEAF1, LEAF2}
    assert trace["children_sum"] == 50.0


def test_trace_by_face_alias_coord_resolves_to_subtotal(tmp_path):
    """Tracing the FACE (sheet,row) — which is an alias — must resolve to
    the sub-sheet total that owns the formula (gotcha #21)."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    trace = trace_cascade_source(db, run_id, sheet="SOFP-CuNonCu", row=5)
    assert trace["found"] is True
    assert trace["concept"]["concept_uuid"] == SUBTOTAL
    # The trace exposes the alias coords so the agent sees the cross-sheet link.
    assert any(a["sheet"] == "SOFP-CuNonCu" for a in trace["concept"]["aliases"])


def test_trace_unknown_cell_returns_not_found(tmp_path):
    db, run_id = _seed(tmp_path)
    trace = trace_cascade_source(db, run_id, sheet="NOPE", row=999)
    assert trace["found"] is False


# ---------------------------------------------------------------------------
# Step 6 — apply_fix guarded write
# ---------------------------------------------------------------------------


def test_grounded_leaf_fix_is_applied(tmp_path):
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=35.0, value_status="observed",
        source="misread", evidence="page 42: Freehold land 35", actor="reviewer",
    ))
    assert out.startswith("ok"), out
    conn = sqlite3.connect(str(db))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1),
        ).fetchone()[0]
    finally:
        conn.close()
    assert val == 35.0


def test_ungrounded_fix_is_rejected(tmp_path):
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=99.0, value_status="observed", source="hunch",
        evidence=None, actor="reviewer",
    ))
    assert out.startswith("rejected"), out
    assert "ground" in out.lower()


def test_mark_not_disclosed_clears_a_grounded_false_positive(tmp_path):
    """A grounded not_disclosed write blanks a false-positive leaf
    (peer-review MEDIUM — the reviewer needs a clear/not-disclosed path)."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=None, value_status="not_disclosed",
        source="line absent", evidence="page 30: no such line", actor="reviewer",
    ))
    assert out.startswith("ok"), out
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT value, value_status FROM run_concept_facts "
            "WHERE run_id=? AND concept_uuid=?", (run_id, LEAF1),
        ).fetchone()
    finally:
        conn.close()
    assert row == (None, "not_disclosed")


def test_ungrounded_not_disclosed_is_rejected(tmp_path):
    """Clearing a value still requires grounding — the guard applies."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=None, value_status="not_disclosed",
        source="hunch", evidence=None, actor="reviewer",
    ))
    assert out.startswith("rejected"), out


def test_arithmetic_marker_counts_as_grounded(tmp_path):
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=35.0, value_status="observed", source="reconcile",
        evidence="arithmetic: 20 + 15 = 35", actor="reviewer",
    ))
    assert out.startswith("ok"), out


def test_arithmetic_plug_into_catchall_row_is_rejected(tmp_path):
    # An arithmetic-only value on a catch-all row IS a balancing plug (#17).
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=OTHER, period="CY", entity_scope="Company",
        value=12.0, value_status="observed", source="balance",
        evidence="arithmetic: 50 - 38 = 12", actor="reviewer",
    ))
    assert out.startswith("rejected"), out
    assert "catch-all" in out.lower() or "residual" in out.lower()


def test_pdf_grounded_write_to_catchall_row_is_allowed(tmp_path):
    # A PDF-cited disclosure on an "Other …" leaf is a genuine fix, not a plug
    # — it must pass the guard (the catch-all is a real disclosed line).
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=OTHER, period="CY", entity_scope="Company",
        value=12.0, value_status="observed", source="read off PDF",
        evidence="page 42: Other property, plant and equipment 12", actor="reviewer",
    ))
    assert out.startswith("ok"), out


def test_plug_into_abstract_row_is_rejected(tmp_path):
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=ABSTRACT, period="CY", entity_scope="Company",
        value=12.0, value_status="observed", source="x",
        evidence="page 42", actor="reviewer",
    ))
    assert out.startswith("rejected"), out
    assert "abstract" in out.lower() or "section header" in out.lower()


def test_guard_unit_passes_grounded_leaf(tmp_path):
    db, run_id = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        leaf = conn.execute(
            "SELECT concept_uuid, kind, canonical_label, render_sheet, "
            "render_row FROM concept_nodes WHERE concept_uuid = ?", (LEAF1,),
        ).fetchone()
    finally:
        conn.close()
    assert evaluate_apply_fix_guard(leaf, evidence="page 42") is None


# ---------------------------------------------------------------------------
# Total-override invariant (gotcha #21, via facts_api) — pinned on the reviewer
# path after the 2026-06-03 run-38 incident, where a total was forced above its
# itemised leaves. A bare observed write to a COMPUTED total is refused; the
# ONLY sanctioned override is children_status='aggregate_only'.
# ---------------------------------------------------------------------------


def test_bare_override_of_computed_total_is_rejected(tmp_path):
    """A grounded value-override of a formula total with no aggregate_only
    marker is refused (the reviewer must fix the leaf or pass aggregate_only)."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    _wf(db, run_id, LEAF2, 20.0)  # children sum = 50
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=SUBTOTAL, period="CY", entity_scope="Company",
        value=80.0, value_status="observed", source="page total",
        evidence="page 7: Total PPE 80", actor="reviewer",
    ))
    assert out.startswith("rejected"), out
    assert "aggregate_only" in out.lower()
    # The override must NOT have landed — the total stays as the agent left it.
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, SUBTOTAL),
        ).fetchone()
    finally:
        conn.close()
    assert row is None  # never written


def test_aggregate_only_override_of_total_is_allowed(tmp_path):
    """An explicit aggregate_only override is the sanctioned escape hatch for a
    total the source discloses but does not itemise."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    _wf(db, run_id, LEAF2, 20.0)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=SUBTOTAL, period="CY", entity_scope="Company",
        value=80.0, value_status="observed", children_status="aggregate_only",
        source="not itemised", evidence="page 7: Total PPE 80 (bundled)",
        actor="reviewer",
    ))
    assert out.startswith("ok"), out
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT value, children_status FROM run_concept_facts "
            "WHERE run_id=? AND concept_uuid=?", (run_id, SUBTOTAL),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 80.0 and row[1] == "aggregate_only"


# ---------------------------------------------------------------------------
# Item 14 — apply_fix rejection telemetry (per-kind tally, guard text unchanged)
# ---------------------------------------------------------------------------


def test_rejection_tally_counts_each_kind(tmp_path):
    """Each guard branch + the computed-override refusal bumps its own kind in
    the rejections dict; a successful write bumps nothing (item 14)."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    _wf(db, run_id, LEAF2, 20.0)
    rej: dict = {}

    # ungrounded — empty evidence.
    apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=31.0, value_status="observed", source="x", evidence=None,
        actor="reviewer"), rejections=rej)
    # abstract_row — write to the ABSTRACT header.
    apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=ABSTRACT, period="CY", entity_scope="Company",
        value=1.0, value_status="observed", source="x", evidence="page 1",
        actor="reviewer"), rejections=rej)
    # catchall_plug — arithmetic value on the "Other …" leaf.
    apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=OTHER, period="CY", entity_scope="Company",
        value=12.0, value_status="observed", source="x",
        evidence="arithmetic: 50 - 38", actor="reviewer"), rejections=rej)
    # computed_override — bare observed write to the formula total.
    apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=SUBTOTAL, period="CY", entity_scope="Company",
        value=80.0, value_status="observed", source="x",
        evidence="page 7: total 80", actor="reviewer"), rejections=rej)

    assert rej == {
        "ungrounded": 1, "abstract_row": 1,
        "catchall_plug": 1, "computed_override": 1,
    }

    # A grounded leaf fix tallies nothing.
    before = dict(rej)
    apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=35.0, value_status="observed", source="fix",
        evidence="page 7: land 35", actor="reviewer"), rejections=rej)
    assert rej == before


def test_classify_guard_returns_kind_and_message(tmp_path):
    """The classifier exposes the kind while the message text is unchanged
    (the model-facing contract evaluate_apply_fix_guard still returns)."""
    from correction.reviewer_agent import classify_apply_fix_guard

    db, run_id = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        abstract = conn.execute(
            "SELECT concept_uuid, kind, canonical_label, render_sheet, "
            "render_row FROM concept_nodes WHERE concept_uuid = ?", (ABSTRACT,),
        ).fetchone()
        leaf = conn.execute(
            "SELECT concept_uuid, kind, canonical_label, render_sheet, "
            "render_row FROM concept_nodes WHERE concept_uuid = ?", (LEAF1,),
        ).fetchone()
    finally:
        conn.close()

    kind, msg = classify_apply_fix_guard(abstract, evidence="page 1")
    assert kind == "abstract_row"
    assert msg == evaluate_apply_fix_guard(abstract, evidence="page 1")

    kind2, msg2 = classify_apply_fix_guard(leaf, evidence="page 42")
    assert kind2 is None and msg2 is None


def test_apply_fix_without_rejections_dict_is_safe(tmp_path):
    """The pure-function call shape (no rejections dict) still works — telemetry
    is optional, guard behaviour unchanged."""
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=1.0, value_status="observed", source="x", evidence=None,
        actor="reviewer"))
    assert out.startswith("rejected")


# ---------------------------------------------------------------------------
# Peer-review P1 — (sheet, row) resolution must be template-family scoped.
# ---------------------------------------------------------------------------


def test_resolve_concept_is_template_scoped(tmp_path):
    """concept_nodes holds every imported standard×level at the same coords,
    so a (sheet,row) trace must resolve within the run's family — otherwise
    the reviewer traces/writes the wrong template's tree (peer-review P1)."""
    db, run_id = _seed(tmp_path)
    GROUP_UID = "00000000-0000-0000-0000-0000000000e9"
    conn = sqlite3.connect(str(db))
    try:
        # A GROUP-family node at the SAME (sheet, row) as company LEAF1.
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES ('mfrs-group-sofp-test-v1', 'g.xlsx', 'linear')")
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, 'mfrs-group-sofp-test-v1', 'LEAF', 'Freehold land', "
            "'SOFP-Sub-CuNonCu', 36, 'B')", (GROUP_UID,))
        conn.commit()
    finally:
        conn.close()

    # Company family resolves the company leaf...
    co = trace_cascade_source(db, run_id, sheet="SOFP-Sub-CuNonCu", row=36,
                              template_prefix="mfrs-company-")
    assert co["found"] is True
    assert co["concept"]["concept_uuid"] == LEAF1
    # ...Group family resolves the group node at the same coord, not company.
    gp = trace_cascade_source(db, run_id, sheet="SOFP-Sub-CuNonCu", row=36,
                              template_prefix="mfrs-group-")
    assert gp["found"] is True
    assert gp["concept"]["concept_uuid"] == GROUP_UID


# ---------------------------------------------------------------------------
# Item 25 — reviewer reverse-lookup tool (find_candidate_rows)
# ---------------------------------------------------------------------------


def test_find_candidate_rows_matches_value_within_tolerance(tmp_path):
    from correction.reviewer_agent import find_candidate_rows

    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 1595.0)   # Freehold land
    _wf(db, run_id, LEAF2, 999.0)    # Buildings
    # ±1 tolerance: 1595.4 matches LEAF1, not LEAF2.
    cands = find_candidate_rows(db, run_id, value=1595.4)
    uuids = {c["concept_uuid"] for c in cands}
    assert LEAF1 in uuids and LEAF2 not in uuids
    hit = next(c for c in cands if c["concept_uuid"] == LEAF1)
    assert hit["sheet"] == "SOFP-Sub-CuNonCu" and hit["row"] == 36
    assert hit["label"] == "Freehold land"


def test_find_candidate_rows_matches_label_hint(tmp_path):
    from correction.reviewer_agent import find_candidate_rows

    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 1595.0)
    _wf(db, run_id, LEAF2, 50.0)
    # Value far off, but the label hint resolves Buildings (LEAF2).
    cands = find_candidate_rows(db, run_id, value=9_999_999, label_hint="buildings")
    assert any(c["concept_uuid"] == LEAF2 for c in cands)


def test_find_candidate_rows_is_template_scoped(tmp_path):
    """The MFRS/MPERS same-(sheet,row) trap (gotcha #21): an unscoped lookup
    would surface a different family's row with the same value."""
    from correction.reviewer_agent import find_candidate_rows

    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 1595.0)
    GROUP_UID = "00000000-0000-0000-0000-0000000000e9"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES ('mfrs-group-sofp-test-v1', 'g.xlsx', 'linear')")
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, 'mfrs-group-sofp-test-v1', 'LEAF', 'Freehold land', "
            "'SOFP-Sub-CuNonCu', 36, 'B')", (GROUP_UID,))
        conn.commit()  # commit BEFORE write_fact (separate connection)
    finally:
        conn.close()
    # A fact for the group-family node with the same value.
    from concept_model.facts_api import write_fact, FactWrite
    write_fact(db, run_id, FactWrite(
        concept_uuid=GROUP_UID, period="CY", entity_scope="Company",
        value=1595.0, value_status="observed", source="x", actor="agent"))

    co = find_candidate_rows(db, run_id, value=1595.0, template_prefix="mfrs-company-")
    uuids = {c["concept_uuid"] for c in co}
    assert LEAF1 in uuids and GROUP_UID not in uuids


def test_find_candidate_rows_caps_results(tmp_path):
    from correction.reviewer_agent import find_candidate_rows

    db, run_id = _seed(tmp_path)
    # 15 distinct concepts all sharing the same value → capped at 10.
    conn = sqlite3.connect(str(db))
    from concept_model.facts_api import write_fact, FactWrite
    for i in range(15):
        uid = f"00000000-0000-0000-0000-0000000a{i:04d}"
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, ?, 'LEAF', ?, 'SOFP-Sub-CuNonCu', ?, 'B')",
            (uid, _TEMPLATE, f"Row {i}", 100 + i))
        conn.commit()
        write_fact(db, run_id, FactWrite(
            concept_uuid=uid, period="CY", entity_scope="Company", value=42.0,
            value_status="observed", source="x", actor="agent"))
    conn.close()
    cands = find_candidate_rows(db, run_id, value=42.0)
    assert len(cands) == 10


def test_find_candidate_rows_honours_group_entity_scope(tmp_path):
    from correction.reviewer_agent import find_candidate_rows
    from concept_model.facts_api import write_fact, FactWrite

    db, run_id = _seed(tmp_path)
    # Same concept, two scopes, same value — the scope filter narrows it.
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company", value=1595.0,
        value_status="observed", source="x", actor="agent"))
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Group", value=1595.0,
        value_status="observed", source="x", actor="agent"))
    grp = find_candidate_rows(db, run_id, value=1595.0, entity_scope="Group")
    assert grp and all(c["entity_scope"] == "Group" for c in grp)


def _get_registered_tool(agent, name):
    """Find the registered pydantic-ai Tool by its MODEL-FACING name."""
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict) and name in tools:
            return tools[name]
    raise AssertionError(f"tool {name!r} not registered on the agent")


def test_wired_find_candidate_rows_tool_delegates_not_recurses(tmp_path):
    """Pin the WIRED tool, not just the module helper.

    A previous same-named ``@agent.tool`` wrapper shadowed the module-level
    ``find_candidate_rows`` helper and recursively invoked the TOOL itself —
    ``TypeError: got multiple values for argument 'value'`` on every live
    call. The wrapper must keep the model-facing name ``find_candidate_rows``
    (prompts/reviewer.md advertises it) while its Python identifier differs.
    """
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    from correction.reviewer_agent import create_reviewer_agent

    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 1595.0)

    agent, deps = create_reviewer_agent(
        model=TestModel(call_tools=[]), db_path=db, run_id=run_id)
    tool = _get_registered_tool(agent, "find_candidate_rows")
    # The Python identifier must differ from the model-facing name, or the
    # wrapper shadows the module helper it delegates to and recurses.
    assert tool.function.__name__ != "find_candidate_rows"

    # Invoke the registered tool function end-to-end against the seeded DB.
    out = tool.function(SimpleNamespace(deps=deps), 1595.0)
    assert isinstance(out, str)
    assert "SOFP-Sub-CuNonCu!row36" in out
    assert "Freehold land" in out
    assert LEAF1 in out


def test_wired_find_candidate_rows_runs_through_agent_loop(tmp_path):
    """Full pydantic-ai invocation: TestModel calls the tool by its
    advertised name and the run completes (pre-fix this raised TypeError)."""
    from pydantic_ai.models.test import TestModel
    from correction.reviewer_agent import create_reviewer_agent

    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 1595.0)

    agent, deps = create_reviewer_agent(
        model=TestModel(call_tools=["find_candidate_rows"]),
        db_path=db, run_id=run_id)
    result = agent.run_sync("review", deps=deps)

    returns = [
        part for msg in result.all_messages()
        for part in getattr(msg, "parts", [])
        if getattr(part, "tool_name", None) == "find_candidate_rows"
        and type(part).__name__ == "ToolReturnPart"
    ]
    assert returns, "find_candidate_rows was never invoked"
    assert all("candidate" in str(r.content) for r in returns)


# ---------------------------------------------------------------------------
# Peer-review P2 — the review packet surfaces filing context + per-check scope.
# ---------------------------------------------------------------------------


def test_review_packet_surfaces_group_scope():
    from correction.reviewer_agent import (
        _format_review_packet, _scope_from_check_name,
    )
    assert _scope_from_check_name("socie_to_sofp_equity [group]") == "Group"
    assert _scope_from_check_name("sopl_to_socie_profit [company]") == "Company"
    assert _scope_from_check_name("plain_check") is None

    packet = _format_review_packet(
        [{"name": "socie_to_sofp_equity [group]", "expected": 1.0,
          "actual": 2.0, "diff": 1.0, "message": "off"}],
        [], None, filing_level="group", filing_standard="mpers")
    assert "MPERS Group" in packet
    assert "GROUP filing" in packet
    assert "entity_scope='Group'" in packet


def test_review_packet_company_filing_has_no_group_block():
    from correction.reviewer_agent import _format_review_packet
    packet = _format_review_packet(
        [{"name": "sofp_assets_balance", "expected": 1.0, "actual": 2.0,
          "diff": 1.0, "message": "off"}],
        [], None, filing_level="company", filing_standard="mfrs")
    assert "MFRS Company" in packet
    assert "GROUP filing" not in packet
    assert "entity_scope=" not in packet  # untagged check → no scope hint


# ---------------------------------------------------------------------------
# Step 7 — raise_flag
# ---------------------------------------------------------------------------


def test_raise_flag_inserts_open_row(tmp_path):
    db, run_id = _seed(tmp_path)
    out = raise_reviewer_flag(
        db, run_id, category="stuck",
        reasoning="cannot reconcile PPE total to the note breakdown",
        target_sheet="SOFP-Sub-CuNonCu", target_row=39, pdf_page=42,
    )
    assert out.startswith("ok"), out
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM reviewer_flags WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["category"] == "stuck"
    assert row["status"] == "open"
    assert row["pdf_page"] == 42
    assert "reconcile" in row["reasoning"]


def test_dispute_with_fix_links_applied_fix(tmp_path):
    db, run_id = _seed(tmp_path)
    raise_reviewer_flag(
        db, run_id, category="disputes_prior",
        reasoning="extraction read 30 but PDF shows 35",
        concept_uuid=LEAF1, applied_fix="revised LEAF1 30 -> 35",
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM reviewer_flags WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["category"] == "disputes_prior"
    assert row["concept_uuid"] == LEAF1
    assert "35" in row["applied_fix"]


def test_raise_flag_rejects_bad_category(tmp_path):
    db, run_id = _seed(tmp_path)
    out = raise_reviewer_flag(db, run_id, category="nonsense", reasoning="x")
    assert out.startswith("rejected")


def test_raise_flag_rejects_empty_reasoning(tmp_path):
    db, run_id = _seed(tmp_path)
    out = raise_reviewer_flag(db, run_id, category="stuck", reasoning="  ")
    assert out.startswith("rejected")


# ---------------------------------------------------------------------------
# Step 5b — list_run_facts: holistic sight (Phase 1)
# ---------------------------------------------------------------------------


def test_list_facts_enumerates_every_filled_fact(tmp_path):
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    _wf(db, run_id, LEAF2, 20.0)
    _wf(db, run_id, OTHER, 5.0)
    facts = list_run_facts(db, run_id, template_prefix="mfrs-company-")
    by_uuid = {f["concept_uuid"]: f for f in facts}
    assert {LEAF1, LEAF2, OTHER} <= set(by_uuid)
    assert by_uuid[LEAF1]["value"] == 30.0
    assert by_uuid[LEAF1]["render_sheet"] == "SOFP-Sub-CuNonCu"


def test_list_facts_flags_a_value_written_to_multiple_rows(tmp_path):
    """The over-count signature: one value disclosed once, written to 2 rows
    (run 153's FVTPL written 3×). _repeated_values must surface it."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 991755.0)
    _wf(db, run_id, OTHER, 991755.0)  # same figure on a different leaf
    facts = list_run_facts(db, run_id, template_prefix="mfrs-company-")
    repeats = _repeated_values(facts)
    assert 991755.0 in repeats
    assert len(repeats[991755.0]) == 2
    # and it shows up in the rendered listing the tool returns
    assert "Repeated values" in _format_fact_listing(facts)


def test_list_facts_does_not_flag_zero(tmp_path):
    """Zero legitimately repeats; treating it as a double-count is noise."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 0.0)
    _wf(db, run_id, LEAF2, 0.0)
    facts = list_run_facts(db, run_id, template_prefix="mfrs-company-")
    assert _repeated_values(facts) == {}


def test_list_facts_does_not_flag_leaf_equal_to_a_computed_total(tmp_path):
    """A leaf whose value equals a COMPUTED total is NOT a double-count — that
    is the shape of every legitimate cross-statement equality the cross-checks
    assert (Total equity = SOCIE equity-at-end, etc.). Restricting
    _repeated_values to LEAF facts keeps those out of the warning so the real
    leaf-vs-leaf over-count isn't buried (review follow-up)."""
    from concept_model.cascade import recompute_after_turn
    db, run_id = _seed(tmp_path)
    # One leaf carries 100; the cascade makes the COMPUTED *Total PPE = 100 too
    # (LEAF2 unwritten). Computed-equals-leaf must NOT be flagged.
    _wf(db, run_id, LEAF1, 100.0)
    recompute_after_turn(db, run_id)
    facts = list_run_facts(db, run_id, template_prefix="mfrs-company-")
    assert any(f["kind"] == "COMPUTED" and f["value"] == 100.0 for f in facts)
    assert _repeated_values(facts) == {}
    # But two LEAVES sharing a value are still flagged (the real over-count).
    _wf(db, run_id, LEAF2, 100.0)
    recompute_after_turn(db, run_id)
    facts = list_run_facts(db, run_id, template_prefix="mfrs-company-")
    assert 100.0 in _repeated_values(facts)


def test_list_facts_sheet_filter_narrows(tmp_path):
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    only = list_run_facts(
        db, run_id, sheet="NoSuchSheet", template_prefix="mfrs-company-")
    assert only == []


def test_list_facts_is_family_scoped(tmp_path):
    """A non-matching family prefix returns nothing (mirrors _resolve_concept)."""
    db, run_id = _seed(tmp_path)
    _wf(db, run_id, LEAF1, 30.0)
    assert list_run_facts(db, run_id, template_prefix="mpers-group-") == []


# ---------------------------------------------------------------------------
# Phase 2 — the review packet renders a check's comparands (both sides)
# ---------------------------------------------------------------------------


def test_packet_renders_comparands_both_sides():
    from correction.reviewer_agent import _format_review_packet
    packet = _format_review_packet(
        failed_checks=[{
            "name": "sopl_to_socie_profit",
            "expected": -20633.0, "actual": -20678.0, "diff": 45.0,
            "message": "mismatch",
            "comparands": [
                {"label": "Profit (loss)", "sheet": "SOPL-Function",
                 "value": -20633.0, "role": "lhs", "statement": "SOPL",
                 "row": None},
                {"label": "Profit (loss)", "sheet": "SOCIE",
                 "value": -20678.0, "role": "rhs", "statement": "SOCIE",
                 "row": None},
            ],
        }],
        conflicts=[], guidance=None,
    )
    # Both sides appear as entry points the reviewer can act on.
    assert "[lhs]" in packet and "[rhs]" in packet
    assert "-20633.0" in packet and "-20678.0" in packet
    assert "SOPL" in packet and "SOCIE" in packet


# --- template-family scoping on the WRITE path (apply_reviewer_fix) ---

def _add_offfamily_leaf(db, uid):
    """Insert one LEAF under a DIFFERENT template family than _seed's
    (mpers-company-… vs _seed's mfrs-company-…)."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES ('mpers-company-sofp-test-v1', 'x.xlsx', 'linear')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, 'mpers-company-sofp-test-v1', 'LEAF', 'Buildings', "
            "'SOFP-Sub-CuNonCu', 37, 'B')",
            (uid,),
        )
        conn.commit()
    finally:
        conn.close()


_OFF_FAMILY = "00000000-0000-0000-0000-0000000000ff"


def test_apply_reviewer_fix_rejects_off_family_concept(tmp_path):
    """A concept_uuid from another template family is refused, not written.

    concept_uuid is a globally-unique PK so the lookup *finds* the concept,
    but writing an off-family fact would land an unrenderable row (gotcha #21).
    Mirrors the read path's template_prefix scoping.
    """
    db, run_id = _seed(tmp_path)
    _add_offfamily_leaf(db, _OFF_FAMILY)

    out = apply_reviewer_fix(
        db, run_id,
        FactWrite(
            concept_uuid=_OFF_FAMILY, period="CY", entity_scope="Company",
            value=999.0, value_status="observed",
            source="why", evidence="page 5: Buildings 999", actor="reviewer",
        ),
        template_prefix="mfrs-company-",
    )
    assert out.startswith("rejected:"), out
    assert "not this run's family" in out
    # And nothing was written for the off-family concept.
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT COUNT(*) FROM run_concept_facts WHERE run_id = ? "
        "AND concept_uuid = ?", (run_id, _OFF_FAMILY),
    ).fetchone()[0]
    conn.close()
    assert n == 0


def test_apply_reviewer_fix_allows_in_family_concept(tmp_path):
    """A grounded write to a concept in the run's own family succeeds."""
    db, run_id = _seed(tmp_path)
    out = apply_reviewer_fix(
        db, run_id,
        FactWrite(
            concept_uuid=LEAF1, period="CY", entity_scope="Company",
            value=1234.0, value_status="observed",
            source="corrected", evidence="page 5: Freehold land 1234",
            actor="reviewer",
        ),
        template_prefix="mfrs-company-",
    )
    assert out.startswith("ok"), out


def test_apply_reviewer_fix_unscoped_still_writes_off_family(tmp_path):
    """Without a template_prefix (pure-fn unit calls) scoping is skipped."""
    db, run_id = _seed(tmp_path)
    _add_offfamily_leaf(db, _OFF_FAMILY)
    out = apply_reviewer_fix(
        db, run_id,
        FactWrite(
            concept_uuid=_OFF_FAMILY, period="CY", entity_scope="Company",
            value=999.0, value_status="observed",
            source="why", evidence="page 5: Buildings 999", actor="reviewer",
        ),
    )
    assert out.startswith("ok"), out
