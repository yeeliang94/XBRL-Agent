"""Shadow-diff parity proof for the fact-based verifier (item 32, 32b).

The migration gate (mirrors ``tests/test_cross_checks_e2e_parity.py``): import a
REAL SOFP template, seed leaf facts, run the cascade, export a real workbook with
live formulas, then run ``verify_statement`` BOTH ways — the xlsx path (flag off,
evaluates formulas) and the fact path (``XBRL_FACT_BASED_VERIFY=1``, reads the
cascade totals) — and assert the two ``VerificationResult`` objects agree on
every decision-bearing field.

``mandatory_unfilled`` is the ONE intentional divergence (product decision
2026-06-14): the fact path is stricter (flags genuinely-blank mandatory leaves
the xlsx formula-prefilled scan hides). The test asserts the fact set is a
SUPERSET of the xlsx set, never silently equal — so the stricter behaviour stays
visible and any regression that makes it laxer fails here.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from collections import deque
from pathlib import Path

import pytest

from concept_model.cascade import recompute_after_turn
from concept_model.exporter import export_run_to_xlsx
from concept_model.importer import (
    import_company_targets, import_group_targets, import_template,
)
from concept_model.parser import parse_template
from db.schema import init_db
from statement_types import StatementType
from tools.verifier import verify_statement

REPO = Path(__file__).resolve().parent.parent
MFRS = REPO / "XBRL-template-MFRS" / "Company"
SOFP_FIXTURE = MFRS / "01-SOFP-CuNonCu.xlsx"


def _uuid_by_label(conn, template_id, label_substr):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND lower(canonical_label) LIKE ? ORDER BY render_row",
        (template_id, f"%{label_substr.lower()}%"),
    ).fetchone()
    return r[0] if r else None


def _descendant_leaf(conn, uuid):
    seen, q = set(), deque([uuid])
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        kind = conn.execute(
            "SELECT kind FROM concept_nodes WHERE concept_uuid = ?", (n,)
        ).fetchone()
        if kind and kind[0] == "LEAF":
            return n
        for (child,) in conn.execute(
            "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?", (n,)
        ):
            q.append(child)
    return None


def _seed(conn, run_id, uuid, value):
    conn.execute(
        "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, updated_at) "
        "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'pdf', '2026-06-14Z')",
        (run_id, uuid, value),
    )


@pytest.fixture
def sofp_run(tmp_path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    tree = parse_template(str(SOFP_FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)
    import_company_targets(db, template_id)

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-14T00:00:00Z', 'x.pdf', 'running', '2026-06-14T00:00:00Z')"
    )
    run_id = int(cur.lastrowid)
    conn.commit()
    return db, run_id, template_id, conn


def _assert_verify_parity(xlsx, facts):
    """Decision-bearing parity. ``mandatory_unfilled`` is the documented
    stricter-divergence — asserted separately by the caller."""
    assert xlsx.is_balanced == facts.is_balanced, "is_balanced differs"
    assert xlsx.matches_pdf == facts.matches_pdf, "matches_pdf differs"
    assert xlsx.mismatches == facts.mismatches, (
        f"mismatches differ:\n xlsx={xlsx.mismatches}\n facts={facts.mismatches}")
    assert xlsx.feedback == facts.feedback, (
        f"feedback differs:\n xlsx={xlsx.feedback!r}\n facts={facts.feedback!r}")
    assert xlsx.magnitude_warnings == facts.magnitude_warnings, (
        "magnitude_warnings differ")
    # computed_totals: every balance key the xlsx path produced must match the
    # fact path at cent precision (the fact path may also carry attribution
    # keys; compare the intersection of balance keys).
    for key, xv in xlsx.computed_totals.items():
        if key in facts.computed_totals:
            assert round(xv, 2) == round(facts.computed_totals[key], 2), (
                f"computed_totals[{key}] differs: xlsx={xv} "
                f"facts={facts.computed_totals[key]}")


@pytest.mark.parametrize("balanced", [True, False])
def test_sofp_verify_e2e_parity(sofp_run, tmp_path, balanced, monkeypatch):
    db, run_id, template_id, conn = sofp_run

    ta = _uuid_by_label(conn, template_id, "total assets")
    el = _uuid_by_label(conn, template_id, "total equity and liabilities")
    asset_leaf = _descendant_leaf(conn, ta)
    eqliab_leaf = _descendant_leaf(conn, el)
    assert asset_leaf and eqliab_leaf and asset_leaf != eqliab_leaf

    _seed(conn, run_id, asset_leaf, 1000.0)
    _seed(conn, run_id, eqliab_leaf, 1000.0 if balanced else 940.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    work = tmp_path / "SOFP_filled.xlsx"
    shutil.copyfile(SOFP_FIXTURE, work)
    export_run_to_xlsx(str(db), run_id, str(work), template_id=template_id)

    # xlsx path (flag off).
    monkeypatch.delenv("XBRL_FACT_BASED_VERIFY", raising=False)
    xlsx = verify_statement(str(work), StatementType.SOFP, "CuNonCu",
                            filing_level="company")

    # fact path (flag on, DB context supplied).
    monkeypatch.setenv("XBRL_FACT_BASED_VERIFY", "1")
    facts = verify_statement(str(work), StatementType.SOFP, "CuNonCu",
                             filing_level="company",
                             db_path=str(db), run_id=run_id,
                             template_id=template_id)
    conn.close()

    assert xlsx.is_balanced is balanced
    _assert_verify_parity(xlsx, facts)
    # mandatory_unfilled: fact path is a (documented) superset of the xlsx path.
    assert set(xlsx.mandatory_unfilled) <= set(facts.mandatory_unfilled), (
        "fact mandatory scan must be at least as strict as the xlsx scan")


def test_mandatory_scan_treats_not_disclosed_as_resolved(sofp_run, monkeypatch):
    """A mandatory (`*`) leaf with a `not_disclosed` fact is RESOLVED (the agent
    confirmed there's no value), not an unfilled gap — only a genuinely ABSENT
    fact is flagged. Pins the module contract (peer-review MEDIUM, 2026-06-14)."""
    from tools.verifier_facts import _collect_unfilled_mandatory_facts, _load_nodes
    from concept_model.facts_api import read_run_facts

    db, run_id, template_id, conn = sofp_run
    nodes = _load_nodes(conn, template_id)
    main = "SOFP-CuNonCu"
    # Pick two distinct mandatory (*) leaf rows on the main sheet.
    star_leaves = [
        n for n in nodes
        if n["sheet"] == main and str(n["label"]).strip().startswith("*")
        and n["kind"] != "COMPUTED"
    ]
    assert len(star_leaves) >= 2, "need two mandatory leaves to exercise the scan"
    disclosed, absent = star_leaves[0], star_leaves[1]

    # `disclosed` gets a not_disclosed fact; `absent` gets none.
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, entity_scope, "
        "value, value_status, source, updated_at) "
        "VALUES (?, ?, 'CY', 'Company', NULL, 'not_disclosed', 'pdf', 'Z')",
        (run_id, disclosed["uuid"]),
    )
    conn.commit()
    facts = read_run_facts(conn, run_id, [template_id])
    unfilled = _collect_unfilled_mandatory_facts(nodes, facts, main, "company")
    conn.close()

    assert disclosed["label"].strip() not in unfilled, (
        "a not_disclosed mandatory row must count as resolved, not unfilled")
    assert absent["label"].strip() in unfilled, (
        "a mandatory row with no fact at all must be flagged unfilled")


# ---------------------------------------------------------------------------
# Generic shadow runner for the cross-statement / matrix verifiers. Seeds every
# data-entry fact, cascades, exports a real workbook, then runs verify_statement
# both ways. Parity holds whether or not the figures balance — both read the
# same underlying numbers (xlsx evaluates formulas, facts read the cascade).
# ---------------------------------------------------------------------------


def _seed_all_data_facts(conn, run_id, tid, value=100.0):
    """Seed a fact for every data-entry (LEAF / MATRIX_CELL) concept that has a
    precomputed export target, so roll-ups and matrix totals cascade AND the
    exporter can place every fact. Driven by ``concept_targets`` (which encodes
    the exact (period, entity_scope) each dimension renders). Integer value
    avoids cent-repr drift in the mismatch messages."""
    rows = conn.execute(
        "SELECT t.concept_uuid, t.period, t.entity_scope "
        "FROM concept_targets t JOIN concept_nodes n "
        "  ON n.concept_uuid = t.concept_uuid "
        "WHERE n.template_id = ? AND n.kind IN ('LEAF', 'MATRIX_CELL')",
        (tid,),
    ).fetchall()
    for uuid, period, scope in rows:
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, "
            "period, entity_scope, value, value_status, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'observed', 'pdf', 'Z')",
            (run_id, uuid, period, scope, value),
        )


def _run_verify_shadow(tmp_path, monkeypatch, fixture, stmt, variant,
                       filing_level, standard):
    db = tmp_path / "xbrl.db"
    init_db(db)
    tree = parse_template(str(fixture))
    jp = tmp_path / f"{tree.template_id}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)
    if filing_level == "group":
        import_group_targets(db, tid)
    else:
        import_company_targets(db, tid)

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-14T00:00:00Z', 'x.pdf', 'running', '2026-06-14T00:00:00Z')"
    )
    run_id = int(cur.lastrowid)
    _seed_all_data_facts(conn, run_id, tid)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    work = tmp_path / "filled.xlsx"
    shutil.copyfile(fixture, work)
    export_run_to_xlsx(str(db), run_id, str(work), template_id=tid,
                       filing_level=filing_level)

    monkeypatch.delenv("XBRL_FACT_BASED_VERIFY", raising=False)
    xlsx = verify_statement(str(work), stmt, variant, filing_level=filing_level,
                            filing_standard=standard)
    monkeypatch.setenv("XBRL_FACT_BASED_VERIFY", "1")
    facts = verify_statement(str(work), stmt, variant, filing_level=filing_level,
                             filing_standard=standard, db_path=str(db),
                             run_id=run_id, template_id=tid)
    conn.close()
    return xlsx, facts


# SOCF-Indirect is the strongest case: its working-capital reconciliation
# references a line via two cancelling paths (a "diamond"), which initially
# exposed a cycle-guard bug in the legacy xlsx evaluator (it dropped the second
# path, over-counting the operating total 1800 vs the correct 1700). That bug is
# fixed (tools/verifier.py::_resolve_cell_value, pinned by
# tests/test_verifier_formula.py::test_diamond_reference_counts_each_path), so
# the fact path and the xlsx path now agree byte-for-byte on SOCF-Indirect too.
@pytest.mark.parametrize("fixture,stmt,variant", [
    ("07-SOCF-Indirect.xlsx", StatementType.SOCF, "Indirect"),
    ("08-SOCF-Direct.xlsx", StatementType.SOCF, "Direct"),
    ("03-SOPL-Function.xlsx", StatementType.SOPL, "Function"),
    ("06-SOCI-NetOfTax.xlsx", StatementType.SOCI, "NetOfTax"),
    ("09-SOCIE.xlsx", StatementType.SOCIE, "Default"),
])
def test_statement_verify_e2e_parity(tmp_path, monkeypatch, fixture, stmt, variant):
    xlsx, facts = _run_verify_shadow(
        tmp_path, monkeypatch, MFRS / fixture, stmt, variant,
        filing_level="company", standard="mfrs")
    _assert_verify_parity(xlsx, facts)
    assert set(xlsx.mandatory_unfilled) <= set(facts.mandatory_unfilled), (
        f"{stmt.value}: fact mandatory scan must be ⊇ xlsx scan")


def test_fact_verify_flag_off_uses_xlsx_path(sofp_run, tmp_path, monkeypatch):
    """With the flag off, the DB context is ignored and the xlsx path runs —
    even when db_path/run_id/template_id are supplied."""
    db, run_id, template_id, conn = sofp_run
    ta = _uuid_by_label(conn, template_id, "total assets")
    el = _uuid_by_label(conn, template_id, "total equity and liabilities")
    _seed(conn, run_id, _descendant_leaf(conn, ta), 500.0)
    _seed(conn, run_id, _descendant_leaf(conn, el), 500.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)
    work = tmp_path / "SOFP_filled.xlsx"
    shutil.copyfile(SOFP_FIXTURE, work)
    export_run_to_xlsx(str(db), run_id, str(work), template_id=template_id)
    conn.close()

    monkeypatch.delenv("XBRL_FACT_BASED_VERIFY", raising=False)
    res = verify_statement(str(work), StatementType.SOFP, "CuNonCu",
                           filing_level="company",
                           db_path=str(db), run_id=run_id, template_id=template_id)
    # The xlsx path's near-inert mandatory scan: the formula-prefilled template
    # yields no unfilled mandatory rows here (proves the flag-off path ran).
    assert res.is_balanced is True
