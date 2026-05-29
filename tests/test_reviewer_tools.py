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
