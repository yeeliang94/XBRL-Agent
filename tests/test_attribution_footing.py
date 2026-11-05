"""Pins for the SOPL / SOCI attribution-footing cross-checks.

These close the same class as the SOCF articulation gap (run-50) for the two
OTHER statements where two subtotals are computed from INDEPENDENT leaf sets:

* SOPL: ``*Profit (loss)`` (down the income statement) vs ``*Total profit
  (loss)`` (from the owners/NCI attribution leaves).
* SOCI: ``*Total comprehensive income`` (= profit + OCI) vs its attribution
  twin (= owners + NCI).

Key behaviours pinned: the check FAILS when the attribution split doesn't sum
to the income-side total, PASSES when it does, and FAILS on a Company filing
whose owners leaf is blank. On a Group scope an undisclosed attribution stays
``not_applicable``. MFRS SOPL-Nature is covered separately because it labels its
attribution total "Profit (loss)", the same as the income row.
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path

import pytest

from concept_model.cascade import recompute_after_turn
from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from cross_checks.attribution_footing import (
    SOCIAttributionFootingCheck, SOPLAttributionFootingCheck,
)
from cross_checks.framework import FactsContext, build_default_cross_checks
from db.schema import init_db
from statement_types import StatementType

REPO = Path(__file__).resolve().parent.parent

CASES = {
    "sopl": dict(
        fixture=REPO / "XBRL-template-MFRS" / "Company" / "03-SOPL-Function.xlsx",
        stmt=StatementType.SOPL,
        check=SOPLAttributionFootingCheck,
        income_label="profit (loss)",
        owners_label="profit (loss), attributable to owners of parent",
    ),
    "sopl_nature": dict(
        fixture=REPO / "XBRL-template-MFRS" / "Company" / "04-SOPL-Nature.xlsx",
        stmt=StatementType.SOPL,
        check=SOPLAttributionFootingCheck,
        income_label="profit (loss)",
        owners_label="profit (loss), attributable to owners of parent",
    ),
    "soci": dict(
        fixture=REPO / "XBRL-template-MFRS" / "Company" / "05-SOCI-BeforeTax.xlsx",
        stmt=StatementType.SOCI,
        check=SOCIAttributionFootingCheck,
        income_label="total comprehensive income",
        owners_label="comprehensive income, attributable to owners of parent",
    ),
}


def _computed_exact(conn, tid, label):
    target = label.strip().lstrip("*").strip().lower()
    for cu, clabel in conn.execute(
        "SELECT concept_uuid, canonical_label FROM concept_nodes "
        "WHERE template_id = ? AND kind = 'COMPUTED' ORDER BY render_row", (tid,),
    ):
        if str(clabel).strip().lstrip("*").strip().lower() == target:
            return cu
    return None


def _leaf_like(conn, tid, substr):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND kind = 'LEAF' AND lower(canonical_label) LIKE ? ORDER BY render_row",
        (tid, f"%{substr.lower()}%"),
    ).fetchone()
    return r[0] if r else None


def _descendant_leaf(conn, uuid):
    seen, q = set(), deque([uuid])
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        k = conn.execute("SELECT kind FROM concept_nodes WHERE concept_uuid = ?", (n,)).fetchone()
        if k and k[0] == "LEAF":
            return n
        for (c,) in conn.execute("SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?", (n,)):
            q.append(c)
    return None


def _seed(conn, run_id, uuid, value, period="CY"):
    conn.execute(
        "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, updated_at) "
        "VALUES (?, ?, ?, 'Company', ?, 'observed', 'pdf', '2026-06-21Z')",
        (run_id, uuid, period, value),
    )


def _val(conn, run_id, uuid, period="CY"):
    r = conn.execute(
        "SELECT value FROM run_concept_facts WHERE run_id = ? AND concept_uuid = ? "
        "AND period = ? AND entity_scope = 'Company'", (run_id, uuid, period),
    ).fetchone()
    return r[0] if r else None


def _setup(tmp_path, case):
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    tree = parse_template(str(case["fixture"]))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)
    import_company_targets(db, tid)
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-21T00:00:00Z', 'x.pdf', 'running', '2026-06-21T00:00:00Z')")
    return db, int(cur.lastrowid), tid, conn


@pytest.mark.parametrize("case_key", ["sopl", "sopl_nature", "soci"])
@pytest.mark.parametrize("articulates", [True, False])
@pytest.mark.parametrize("attribution_source", ["owners", "nci"])
@pytest.mark.parametrize("period", ["CY", "PY"])
def test_attribution_footing_catches_mismatch(tmp_path, case_key, articulates, attribution_source, period):
    case = CASES[case_key]
    db, run_id, tid, conn = _setup(tmp_path, case)

    income_uuid = _computed_exact(conn, tid, case["income_label"])
    owners = _leaf_like(conn, tid, case["owners_label"])
    assert income_uuid and owners, "concepts not resolved"
    income_leaf = _descendant_leaf(conn, income_uuid)
    assert income_leaf

    # Seed one income leaf, cascade to compute the income-side total, then set
    # the owners attribution leaf to match it (articulates) or miss by a clear
    # gap (does not).
    _seed(conn, run_id, income_leaf, 1000.0, period)
    if period == "PY":
        _seed(conn, run_id, income_leaf, 1000.0)
        _seed(conn, run_id, owners, 1000.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)
    p = _val(conn, run_id, income_uuid, period)
    assert p is not None

    attribution_leaf = owners if attribution_source == "owners" else _leaf_like(
        conn, tid, case["owners_label"].replace("owners of parent", "non-controlling interests"))
    assert attribution_leaf
    _seed(conn, run_id, attribution_leaf, p if articulates else p + 50.0, period)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    ctx = FactsContext(
        conn=conn, run_id=run_id, template_ids={case["stmt"]: tid},
        filing_level="company", filing_standard="mfrs")
    result = case["check"]().run_facts(ctx, tolerance=1.0)
    from concept_model.exporter import export_run_to_xlsx
    import shutil
    workbook = tmp_path / "filled.xlsx"
    shutil.copyfile(case["fixture"], workbook)
    export_run_to_xlsx(db, run_id, str(workbook), template_id=tid)
    workbook_result = case["check"]().run({case["stmt"]: str(workbook)}, tolerance=1.0)
    conn.close()
    expected = "passed" if articulates and attribution_source == "owners" else "failed"
    assert result.status == expected, result.message
    assert workbook_result.status == expected, workbook_result.message


@pytest.mark.parametrize("case_key", ["sopl", "sopl_nature", "soci"])
def test_attribution_footing_fails_when_company_owners_undisclosed(tmp_path, case_key):
    """No attribution leaves filled on a Company filing → failed. The
    mandatory attribution total is a formula over the owners leaf, so a blank
    owners leaf leaves it empty; the agent must carry the full figure there."""
    case = CASES[case_key]
    db, run_id, tid, conn = _setup(tmp_path, case)
    income_uuid = _computed_exact(conn, tid, case["income_label"])
    income_leaf = _descendant_leaf(conn, income_uuid)
    _seed(conn, run_id, income_leaf, 1000.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    ctx = FactsContext(
        conn=conn, run_id=run_id, template_ids={case["stmt"]: tid},
        filing_level="company", filing_standard="mfrs")
    result = case["check"]().run_facts(ctx, tolerance=1.0)
    from tools.verifier_facts import verify_statement_facts
    verification = verify_statement_facts(conn, run_id, tid, case["stmt"])
    assert not verification.is_balanced
    assert any("owners attribution is missing" in m for m in verification.mismatches)
    conn.close()
    assert result.status == "failed", result.message


@pytest.mark.parametrize("case_key", ["sopl", "sopl_nature", "soci"])
@pytest.mark.parametrize("preserved", [True, False])
def test_blank_prior_year_owners_is_only_required_when_writable(tmp_path, case_key, preserved):
    """A rolled-forward PY is read-only, so a blank owners leaf there cannot be
    fixed on this run and must not fail it. An ordinary PY still fails."""
    from tools.verifier_facts import verify_statement_facts
    case = CASES[case_key]
    db, run_id, tid, conn = _setup(tmp_path, case)
    if preserved:
        conn.execute("UPDATE runs SET run_config_json = ? WHERE id = ?",
                     (json.dumps({"prior_year_workbook": "a" * 64}), run_id))
    income_uuid = _computed_exact(conn, tid, case["income_label"])
    owners = _leaf_like(conn, tid, case["owners_label"])
    income_leaf = _descendant_leaf(conn, income_uuid)
    _seed(conn, run_id, income_leaf, 1000.0)
    _seed(conn, run_id, income_leaf, 900.0, "PY")
    conn.commit()
    recompute_after_turn(str(db), run_id)
    _seed(conn, run_id, owners, _val(conn, run_id, income_uuid))
    conn.commit()
    recompute_after_turn(str(db), run_id)

    ctx = FactsContext(
        conn=conn, run_id=run_id, template_ids={case["stmt"]: tid},
        filing_level="company", filing_standard="mfrs")
    result = case["check"]().run_facts(ctx, tolerance=1.0)
    verification = verify_statement_facts(conn, run_id, tid, case["stmt"])
    conn.close()
    assert result.status == ("passed" if preserved else "failed"), result.message
    assert verification.is_balanced == preserved
    if preserved:
        assert "PY" in result.message and "not disclosed" in result.message


@pytest.mark.parametrize("case_key", ["sopl", "sopl_nature", "soci"])
@pytest.mark.parametrize("gap", ["current_year_owners_blank", "prior_year_split_wrong"])
def test_rolled_forward_run_still_reports_real_attribution_gaps(tmp_path, case_key, gap):
    """The prior-year exemption covers a blank PY owners leaf only: a blank CY
    owners leaf and an imported PY split that does not foot both still fail."""
    case = CASES[case_key]
    db, run_id, tid, conn = _setup(tmp_path, case)
    conn.execute("UPDATE runs SET run_config_json = ? WHERE id = ?",
                 (json.dumps({"prior_year_workbook": "a" * 64}), run_id))
    income_uuid = _computed_exact(conn, tid, case["income_label"])
    owners = _leaf_like(conn, tid, case["owners_label"])
    income_leaf = _descendant_leaf(conn, income_uuid)
    _seed(conn, run_id, income_leaf, 1000.0)
    _seed(conn, run_id, income_leaf, 900.0, "PY")
    conn.commit()
    recompute_after_turn(str(db), run_id)
    if gap == "prior_year_split_wrong":
        _seed(conn, run_id, owners, _val(conn, run_id, income_uuid))
        _seed(conn, run_id, owners, _val(conn, run_id, income_uuid, "PY") + 50.0, "PY")
        conn.commit()
        recompute_after_turn(str(db), run_id)

    ctx = FactsContext(
        conn=conn, run_id=run_id, template_ids={case["stmt"]: tid},
        filing_level="company", filing_standard="mfrs")
    result = case["check"]().run_facts(ctx, tolerance=1.0)
    conn.close()
    assert result.status == "failed", result.message


def test_attribution_footing_checks_registered():
    names = {c.name for c in build_default_cross_checks()}
    assert "sopl_attribution_footing" in names
    assert "soci_attribution_footing" in names


def test_group_company_scope_is_checked(tmp_path):
    """Peer-review (Codex P2): on a group filing the check must reconcile the
    COMPANY column too, not just Group. Group attribution foots but Company
    attribution is wrong → overall FAILED (was silently passing)."""
    from concept_model.importer import import_group_targets
    case = CASES["sopl"]
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    tree = parse_template(str(case["fixture"]))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)
    import_group_targets(db, tid)
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-21T00:00:00Z', 'x.pdf', 'running', '2026-06-21T00:00:00Z')")
    run_id = int(cur.lastrowid)
    conn.commit()

    income_uuid = _computed_exact(conn, tid, case["income_label"])
    owners = _leaf_like(conn, tid, case["owners_label"])
    income_leaf = _descendant_leaf(conn, income_uuid)

    def seed_scope(uuid, value, scope):
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, updated_at) "
            "VALUES (?, ?, 'CY', ?, ?, 'observed', 'pdf', 't')",
            (run_id, uuid, scope, value))

    # Group: income leaf + owners attribution that FOOTS.
    seed_scope(income_leaf, 1000.0, "Group")
    # Company: income leaf + owners attribution that is WRONG (off by 50).
    seed_scope(income_leaf, 800.0, "Company")
    conn.commit()
    recompute_after_turn(str(db), run_id)
    g_profit = conn.execute(
        "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=? "
        "AND period='CY' AND entity_scope='Group'", (run_id, income_uuid)).fetchone()[0]
    c_profit = conn.execute(
        "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=? "
        "AND period='CY' AND entity_scope='Company'", (run_id, income_uuid)).fetchone()[0]
    seed_scope(owners, g_profit, "Group")          # Group foots
    seed_scope(owners, c_profit + 50.0, "Company")  # Company is wrong
    conn.commit()
    recompute_after_turn(str(db), run_id)

    ctx = FactsContext(
        conn=conn, run_id=run_id, template_ids={case["stmt"]: tid},
        filing_level="group", filing_standard="mfrs")
    result = SOPLAttributionFootingCheck().run_facts(ctx, tolerance=1.0)
    conn.close()
    assert result.status == "failed", result.message
    assert "company" in result.message.lower()


def _build_sopl_xlsx(path, *, owners, nci, income=1000.0, owners_blank=False):
    """Minimal SOPL-Function sheet: income total + attribution total formula +
    attribution leaf cells. ``owners_blank`` leaves the leaf cells empty."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOPL-Function"
    ws["A1"] = "*Profit (loss)"
    ws["B1"] = income
    ws["A2"] = "Profit (loss), attributable to owners of parent"
    ws["A3"] = "Profit (loss), attributable to non-controlling interests"
    if not owners_blank:
        ws["B2"] = owners
        ws["B3"] = nci
    ws["A4"] = "*Total profit (loss)"
    ws["B4"] = "=B2+B3"
    wb.save(str(path))


def test_xlsx_explicit_zero_attribution_is_validated(tmp_path):
    """Peer-review (Codex P2): an EXPLICIT 0 attribution while income is
    non-zero is a disclosed mismatch — the xlsx path must FAIL it, not skip it
    as not_applicable."""
    path = tmp_path / "sopl_zero.xlsx"
    _build_sopl_xlsx(path, owners=0.0, nci=0.0, income=1000.0)
    result = SOPLAttributionFootingCheck().run(
        {StatementType.SOPL: str(path)}, tolerance=1.0, filing_level="company")
    assert result.status == "failed", result.message


def test_each_year_records_the_tolerance_that_decided_it():
    # 5 off on 10 million passes here, so the review screen must not flag it.
    evaluated = SOPLAttributionFootingCheck()._eval_scope(
        10_000_000, 10_000_005, 1, 2, "SOPL", 1.0, "Company CY", "CY", is_company=False,
    )
    assert evaluated["verdict"] == "passed"
    assert [c.tolerance for c in evaluated["comparands"]] == pytest.approx([10.000005, 10.000005])


def test_xlsx_blank_attribution_fails_on_company(tmp_path):
    """Genuinely BLANK attribution leaves fail a Company filing: the owners
    leaf must carry the full profit so the mandatory total computes."""
    path = tmp_path / "sopl_blank.xlsx"
    _build_sopl_xlsx(path, owners=0.0, nci=0.0, income=1000.0, owners_blank=True)
    result = SOPLAttributionFootingCheck().run(
        {StatementType.SOPL: str(path)}, tolerance=1.0, filing_level="company")
    assert result.status == "failed", result.message
    assert "owners attribution is missing" in result.message
