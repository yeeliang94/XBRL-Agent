"""Phase 0 (item 32) — the shared fact-read + label-resolve primitives.

Covers:
* ``facts_api.read_run_facts`` — uuid-keyed reads incl. COMPUTED totals,
  scoped by template_id, kind-filterable.
* ``label_resolver.resolve_label`` — label→concept with exact-over-substring
  matching and leaf-over-header preference (gotcha #17).
* ``tests/shadow_diff`` — the parity harness self-test.

Hand-built fixtures (no xlsx) keyed on concept_uuid, mirroring
``tests/test_eval_grader.py``.
"""
from __future__ import annotations

import sqlite3

import pytest

from concept_model.facts_api import read_run_facts
from concept_model.label_resolver import resolve_label
from db.schema import init_db
from statement_types import StatementType


_TEMPLATE_A = "mfrs-company-sofp-cunoncu-v1"
_TEMPLATE_B = "mfrs-company-sopl-function-v1"  # a second family to prove scoping

# (uuid, kind, label, sheet, row, col)
_CONCEPTS_A = [
    ("a_cash", "LEAF", "Cash and bank balances", "SOFP-CuNonCu", 5, "B"),
    ("a_recv", "LEAF", "Trade receivables", "SOFP-CuNonCu", 6, "B"),
    # Same label on a header and a leaf — gotcha #17 leaf-over-header case.
    ("a_oth_hdr", "ABSTRACT", "Other income", "SOFP-CuNonCu", 7, "B"),
    ("a_oth_leaf", "LEAF", "Other income", "SOFP-CuNonCu", 8, "B"),
    ("a_assets", "COMPUTED", "Total assets", "SOFP-CuNonCu", 20, "B"),
    ("a_eqliab", "COMPUTED", "Total equity and liabilities", "SOFP-CuNonCu", 40, "B"),
]
_CONCEPTS_B = [
    # Collides on (sheet,row) shape but different template — must stay scoped.
    ("b_rev", "LEAF", "Revenue", "SOPL-Function", 5, "B"),
    ("b_assets", "COMPUTED", "Total assets", "SOPL-Function", 20, "B"),
]


@pytest.fixture
def db(tmp_path):
    dbpath = tmp_path / "facts.db"
    init_db(dbpath)
    c = sqlite3.connect(str(dbpath))
    c.execute("PRAGMA foreign_keys = ON")
    for tid in (_TEMPLATE_A, _TEMPLATE_B):
        c.execute(
            "INSERT INTO concept_templates(template_id, source_path) VALUES (?, ?)",
            (tid, "/tmp/t.xlsx"),
        )
    for tid, concepts in ((_TEMPLATE_A, _CONCEPTS_A), (_TEMPLATE_B, _CONCEPTS_B)):
        for uuid, kind, label, sheet, row, col in concepts:
            c.execute(
                "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
                "canonical_label, render_sheet, render_row, render_col) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uuid, tid, kind, label, sheet, row, col),
            )
    cur = c.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-06-14T00:00:00Z', 'x.pdf', 'completed')"
    )
    run_id = int(cur.lastrowid)
    c.commit()
    yield c, run_id
    c.close()


def _fact(c, run_id, uuid, value, *, status="observed", period="CY",
          scope="Company", source="agent"):
    c.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, uuid, period, scope, value, status, source),
    )


# --------------------------------------------------------------------------
# read_run_facts
# --------------------------------------------------------------------------

def test_read_run_facts_includes_computed_totals(db):
    """Unlike the grader, the verifier read must surface cascade-written
    COMPUTED totals — that's the whole point of item 32."""
    conn, rid = db
    _fact(conn, rid, "a_cash", 100.0)
    _fact(conn, rid, "a_recv", 50.0)
    _fact(conn, rid, "a_assets", 150.0, source="cascade")
    conn.commit()

    facts = read_run_facts(conn, rid, [_TEMPLATE_A])
    assert facts[("a_cash", "CY", "Company")]["value"] == 100.0
    # The COMPUTED total is present (grader would have dropped it).
    total = facts[("a_assets", "CY", "Company")]
    assert total["value"] == 150.0
    assert total["source"] == "cascade"


def test_read_run_facts_kind_filter_excludes_computed(db):
    conn, rid = db
    _fact(conn, rid, "a_cash", 100.0)
    _fact(conn, rid, "a_assets", 100.0, source="cascade")
    conn.commit()

    leaves = read_run_facts(conn, rid, [_TEMPLATE_A], kinds=("LEAF",))
    assert ("a_cash", "CY", "Company") in leaves
    assert ("a_assets", "CY", "Company") not in leaves


def test_read_run_facts_scoped_by_template(db):
    """A fact in template B must not leak into a template-A read."""
    conn, rid = db
    _fact(conn, rid, "a_cash", 100.0)
    _fact(conn, rid, "b_rev", 999.0)
    conn.commit()

    a_only = read_run_facts(conn, rid, [_TEMPLATE_A])
    assert ("a_cash", "CY", "Company") in a_only
    assert ("b_rev", "CY", "Company") not in a_only


def test_read_run_facts_group_and_py_keys_distinct(db):
    conn, rid = db
    _fact(conn, rid, "a_cash", 100.0, period="CY", scope="Company")
    _fact(conn, rid, "a_cash", 90.0, period="PY", scope="Company")
    _fact(conn, rid, "a_cash", 200.0, period="CY", scope="Group")
    conn.commit()

    facts = read_run_facts(conn, rid, [_TEMPLATE_A])
    assert facts[("a_cash", "CY", "Company")]["value"] == 100.0
    assert facts[("a_cash", "PY", "Company")]["value"] == 90.0
    assert facts[("a_cash", "CY", "Group")]["value"] == 200.0


def test_read_run_facts_empty_template_list(db):
    conn, rid = db
    assert read_run_facts(conn, rid, []) == {}


# --------------------------------------------------------------------------
# resolve_label
# --------------------------------------------------------------------------

def test_resolve_label_finds_total_assets(db):
    conn, _ = db
    res = resolve_label(conn, _TEMPLATE_A, "total assets")
    assert res == ("a_assets", "SOFP-CuNonCu", 20, "B")


def test_resolve_label_strips_leading_star_and_is_case_insensitive(db):
    conn, _ = db
    res = resolve_label(conn, _TEMPLATE_A, "*TOTAL EQUITY AND LIABILITIES")
    assert res is not None and res[0] == "a_eqliab"


def test_resolve_label_prefers_leaf_over_header(db):
    """Same label on an ABSTRACT header (row 7) and a LEAF (row 8) → leaf."""
    conn, _ = db
    res = resolve_label(conn, _TEMPLATE_A, "Other income")
    assert res == ("a_oth_leaf", "SOFP-CuNonCu", 8, "B")


def test_resolve_label_is_template_scoped(db):
    """'Total assets' exists in both templates with different uuids — the
    lookup must honour template_id (gotcha #21)."""
    conn, _ = db
    a = resolve_label(conn, _TEMPLATE_A, "total assets")
    b = resolve_label(conn, _TEMPLATE_B, "total assets")
    assert a[0] == "a_assets"
    assert b[0] == "b_assets"


def test_resolve_label_returns_none_when_absent(db):
    conn, _ = db
    assert resolve_label(conn, _TEMPLATE_A, "goodwill on consolidation") is None


def test_read_labelled_value_skips_blank_duplicate_to_populated(db):
    """Peer-review MEDIUM (2026-06-14): when a label matches two concepts and
    the first (topmost) carries no fact but the second does, the reader must
    return the second's value — mirroring find_value_by_label's multi-row
    scan, not stopping at the first concept."""
    from types import SimpleNamespace
    from cross_checks.facts_util import read_labelled_value

    conn, rid = db
    # Two LEAF concepts share the label "Cash at end" — earlier row blank,
    # later row populated.
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('dup_top', ?, 'LEAF', 'Cash at end', 'SOFP-CuNonCu', 50, 'B')",
        (_TEMPLATE_A,),
    )
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('dup_bot', ?, 'LEAF', 'Cash at end', 'SOFP-CuNonCu', 51, 'B')",
        (_TEMPLATE_A,),
    )
    # Only the lower row has a fact (the upper is genuinely absent).
    _fact(conn, rid, "dup_bot", 4242.0)
    conn.commit()

    ctx = SimpleNamespace(
        conn=conn, run_id=rid, template_ids={StatementType.SOFP: _TEMPLATE_A})
    lv = read_labelled_value(ctx, StatementType.SOFP, "cash at end", "CY", "Company")
    assert lv.value == 4242.0
    assert lv.row == 51  # picked the populated (lower) concept, not the blank top


# --------------------------------------------------------------------------
# shadow-diff harness self-test
# --------------------------------------------------------------------------

def test_shadow_harness_passes_identical_and_flags_differences():
    from cross_checks.framework import Comparand, CrossCheckResult
    from tests.shadow_diff import (
        assert_cross_check_parity,
        cross_check_diff,
        nums_equal,
    )

    # Sub-cent difference is treated as equal (cascade rounds to cents).
    assert nums_equal(1000.004, 1000.0)
    assert not nums_equal(1000.02, 1000.0)
    assert nums_equal(None, None)
    assert not nums_equal(1.0, None)

    def _result(value, message):
        return CrossCheckResult(
            name="sofp_balance",
            status="passed",
            expected=value,
            actual=value,
            diff=0.0,
            tolerance=1.0,
            message=message,
            target_sheet="SOFP-CuNonCu",
            target_row=20,
            comparands=[Comparand(label="Total assets", sheet="SOFP-CuNonCu",
                                  value=value, role="lhs", statement="sofp",
                                  row=20)],
        )

    a = _result(1000.0, "ok")
    b = _result(1000.004, "ok")  # sub-cent — still parity
    assert_cross_check_parity(a, b)  # must not raise

    # A wording difference is caught.
    diffs = cross_check_diff(a, _result(1000.0, "DIFFERENT"))
    assert any("message" in d for d in diffs)

    # A >1-cent value difference is caught.
    diffs = cross_check_diff(a, _result(1000.50, "ok"))
    assert diffs
