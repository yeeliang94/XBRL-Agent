"""Reviewer self-verification — the close-the-loop fix.

Pins the capability that turns the reviewer from a single un-verified pass
into one that confirms its own fixes (the 2026-06-25 SOFP-reviewer regression:
the reviewer "fixed" a 223 cash tie-out by zeroing a real disclosed line,
unbalancing the SOFP by 7,572, and nothing fed that NEW failure back to it).

Covers:
  * ``_format_verification`` — distinguishes a STILL-FAILING targeted check
    from a ``⚠ NEW`` failure the reviewer's own edit introduced.
  * ``run_verification_checks`` — re-runs the real cross-check suite against
    the run's CURRENT facts (cascade-recomputed), reflecting reviewer edits.
  * the ``verify_fixes`` agent tool is registered.
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.schema import init_db
from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from correction.reviewer_agent import (
    run_verification_checks,
    _format_verification,
    create_reviewer_agent,
)


REPO = Path(__file__).resolve().parent.parent
SOFP_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


# --------------------------------------------------------------------------
# _format_verification — pure, fast (no DB)
# --------------------------------------------------------------------------

def _result(name, status, **kw):
    return SimpleNamespace(name=name, status=status, **kw)


def test_format_all_pass_reports_clean():
    out = _format_verification(
        [_result("sofp_balance", "passed"), _result("socf_cash", "passed")],
        original_failed_names={"socf_cash"},
    )
    assert "VERIFIED" in out
    assert "introduced none" in out


def test_format_marks_new_failure_as_regression():
    # original failing set did NOT include sofp_balance → the reviewer's edit
    # introduced it → it must be flagged as a regression to reconsider.
    out = _format_verification(
        [_result("sofp_balance", "failed", message="assets 303143 vs E+L 310715")],
        original_failed_names={"socf_to_sofp_cash"},
    )
    assert "NEW" in out
    assert "sofp_balance" in out
    assert "revert" in out.lower()


def test_format_distinguishes_still_failing_from_new():
    out = _format_verification(
        [
            _result("socf_to_sofp_cash", "failed", message="cash off by 223"),
            _result("sofp_balance", "failed", message="assets short by 7572"),
        ],
        original_failed_names={"socf_to_sofp_cash"},
    )
    # The pre-existing target is "still failing", the introduced one is NEW.
    assert "1 of them NEW" in out
    socf_line = next(l for l in out.splitlines() if "socf_to_sofp_cash" in l)
    sofp_line = next(l for l in out.splitlines() if "sofp_balance" in l)
    assert "NEW" not in socf_line
    assert "NEW" in sofp_line


# --------------------------------------------------------------------------
# run_verification_checks — real template + cascade + cross-checks
# --------------------------------------------------------------------------

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
        row = conn.execute(
            "SELECT kind FROM concept_nodes WHERE concept_uuid = ?", (n,)
        ).fetchone()
        if row and row[0] == "LEAF":
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
        "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'pdf', '2026-06-25Z')",
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
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-25T00:00:00Z', 'x.pdf', 'running', '2026-06-25T00:00:00Z')"
    ).lastrowid)
    # The succeeded statement run_verification_checks scopes the checks from.
    conn.execute(
        "INSERT INTO run_agents(run_id, statement_type, variant, model, status, "
        "started_at) VALUES (?, 'SOFP', 'CuNonCu', 'm', 'succeeded', '2026Z')",
        (run_id,),
    )
    conn.commit()
    return db, run_id, template_id, conn


def _balance_result(results):
    return next(
        (r for r in results if getattr(r, "name", "").lower().startswith("sofp")
         and "balance" in getattr(r, "name", "").lower()),
        None,
    )


def test_run_verification_reflects_balanced_facts(sofp_run):
    db, run_id, template_id, conn = sofp_run
    ta = _uuid_by_label(conn, template_id, "total assets")
    el = _uuid_by_label(conn, template_id, "total equity and liabilities")
    asset_leaf = _descendant_leaf(conn, ta)
    eqliab_leaf = _descendant_leaf(conn, el)
    _seed(conn, run_id, asset_leaf, 1000.0)
    _seed(conn, run_id, eqliab_leaf, 1000.0)
    conn.commit()

    results = run_verification_checks(
        str(db), run_id, filing_level="company", filing_standard="mfrs")
    bal = _balance_result(results)
    assert bal is not None and bal.status == "passed", results


def test_run_verification_detects_reviewer_introduced_regression(sofp_run):
    """The exact failure shape: a balanced SOFP, then an edit that unbalances it.

    Mirrors the incident — the reviewer zeroed a real leaf, so total assets
    drops and the previously-PASSING balance check fails. verify_fixes must
    surface that as a NEW failure (it was not in the baseline).
    """
    db, run_id, template_id, conn = sofp_run
    ta = _uuid_by_label(conn, template_id, "total assets")
    el = _uuid_by_label(conn, template_id, "total equity and liabilities")
    asset_leaf = _descendant_leaf(conn, ta)
    eqliab_leaf = _descendant_leaf(conn, el)

    # Start balanced — like the run, SOFP balance was GREEN before the reviewer.
    _seed(conn, run_id, asset_leaf, 1000.0)
    _seed(conn, run_id, eqliab_leaf, 1000.0)
    conn.commit()
    baseline = run_verification_checks(str(db), run_id)
    assert _balance_result(baseline).status == "passed"
    original_failed = {
        r.name for r in baseline if getattr(r, "status", None) == "failed"
    }

    # The reviewer's bad edit: zero a real disclosed asset leaf.
    _seed(conn, run_id, asset_leaf, 0.0)
    conn.commit()
    after = run_verification_checks(str(db), run_id)
    bal = _balance_result(after)
    assert bal is not None and bal.status == "failed", after

    # The formatter must flag this as a regression the reviewer caused.
    summary = _format_verification(after, original_failed)
    assert "NEW" in summary
    assert bal.name in summary


def test_cascade_failure_blocks_verification(sofp_run, monkeypatch):
    """peer-review MEDIUM: if the cascade recompute fails, the COMPUTED totals
    are stale (reviewer writes don't cascade per write), so verification must
    NOT silently run cross-checks against pre-edit totals and return a false
    'VERIFIED'. run_verification_checks raises; the verify_fixes tool maps that
    to a 'could not run' message rather than a green pass.
    """
    import concept_model.cascade as cascade
    import correction.reviewer_agent as ra

    db, run_id, _tid, conn = sofp_run

    def _boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cascade, "recompute_after_turn", _boom)
    with pytest.raises(RuntimeError):
        ra.run_verification_checks(
            str(db), run_id, filing_level="company", filing_standard="mfrs")

    # With recompute disabled the helper does NOT raise (caller's explicit opt-out).
    res = ra.run_verification_checks(
        str(db), run_id, filing_level="company", filing_standard="mfrs",
        recompute=False)
    assert isinstance(res, list)


# --------------------------------------------------------------------------
# Tool registration
# --------------------------------------------------------------------------

def test_factory_registers_verify_fixes_tool(sofp_run):
    from pydantic_ai.models.test import TestModel

    db, run_id, _tid, _conn = sofp_run
    agent, deps = create_reviewer_agent(
        model=TestModel(call_tools=[]), db_path=db, run_id=run_id,
        failed_checks=[{"name": "socf_to_sofp_cash"}],
    )
    names: set = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    assert "verify_fixes" in names
    # The baseline failing set is seeded onto deps for regression detection.
    assert deps.original_failed_names == {"socf_to_sofp_cash"}
