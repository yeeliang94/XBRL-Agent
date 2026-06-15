"""Full-pipeline e2e parity gate for the fact-based cross-checks (item 32, 32a).

The hand-built shadow tests (``test_cross_checks_shadow.py``) prove the two code
paths agree on identical *logical* data. THIS proves they agree on the real
artifacts: a genuine template is imported, leaf facts are seeded, the cascade
computes the totals into ``run_concept_facts``, and the exporter renders a real
workbook with live formulas. Then the xlsx check (evaluating those formulas) and
the fact check (reading the cascade totals) must produce a shadow-equal result.

This is the gate the plan requires before flipping ``XBRL_FACT_BASED_CHECKS`` on:
it exercises real-template label resolution + real cascade-vs-formula arithmetic,
which the hand-built fixtures can't reach. Currently covers SOFP balance
(MFRS Company); extend per statement as their fact paths are proven.
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
from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from cross_checks.framework import FactsContext
from cross_checks.socf_to_sofp_cash import SOCFToSOFPCashCheck
from cross_checks.socie_to_sofp_equity import SOCIEToSOFPEquityCheck
from cross_checks.sofp_balance import SOFPBalanceCheck
from db.schema import init_db
from statement_types import StatementType
from tests.shadow_diff import assert_cross_check_parity, nums_equal


REPO = Path(__file__).resolve().parent.parent
SOFP_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
SOCF_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "07-SOCF-Indirect.xlsx"
SOCIE_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "09-SOCIE.xlsx"


def _uuid_by_label(conn, template_id, label_substr):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND lower(canonical_label) LIKE ? ORDER BY render_row",
        (template_id, f"%{label_substr.lower()}%"),
    ).fetchone()
    return r[0] if r else None


def _descendant_leaf(conn, uuid):
    """BFS down the calc edges from a COMPUTED total to a LEAF descendant."""
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


def _seed(conn, run_id, uuid, value):
    conn.execute(
        "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, updated_at) "
        "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'pdf', '2026-06-14Z')",
        (run_id, uuid, value),
    )


@pytest.mark.parametrize("balanced", [True, False])
def test_sofp_balance_e2e_parity(sofp_run, tmp_path, balanced):
    db, run_id, template_id, conn = sofp_run

    ta = _uuid_by_label(conn, template_id, "total assets")
    el = _uuid_by_label(conn, template_id, "total equity and liabilities")
    assert ta and el, "totals not found in imported template"
    asset_leaf = _descendant_leaf(conn, ta)
    eqliab_leaf = _descendant_leaf(conn, el)
    assert asset_leaf and eqliab_leaf and asset_leaf != eqliab_leaf

    # Seed one leaf under each total. Balanced → equal; else a clear gap.
    _seed(conn, run_id, asset_leaf, 1000.0)
    _seed(conn, run_id, eqliab_leaf, 1000.0 if balanced else 940.0)
    conn.commit()

    # Cascade computes the COMPUTED totals into run_concept_facts.
    recompute_after_turn(str(db), run_id)

    # Export a real workbook (live formulas) the xlsx path will evaluate.
    work = tmp_path / "SOFP_filled.xlsx"
    shutil.copyfile(SOFP_FIXTURE, work)
    export_run_to_xlsx(str(db), run_id, str(work), template_id=template_id)

    xlsx = SOFPBalanceCheck().run(
        {StatementType.SOFP: str(work)}, tolerance=1.0, filing_level="company")

    ctx = FactsContext(
        conn=conn, run_id=run_id,
        template_ids={StatementType.SOFP: template_id},
        filing_level="company", filing_standard="mfrs",
    )
    facts = SOFPBalanceCheck().run_facts(ctx, tolerance=1.0)
    conn.close()

    assert xlsx.status == ("passed" if balanced else "failed")
    # The real gate: cascade-read result == formula-evaluated result.
    assert_cross_check_parity(xlsx, facts)


# --------------------------------------------------------------------------
# Cross-statement + SOCIE-matrix checks on REAL templates (extends the SOFP
# gate). These import two genuine templates into one run, seed leaves, cascade,
# export real workbooks with live formulas, then assert the xlsx check and the
# fact check produce a shadow-equal result — proving real-template label /
# matrix-column resolution agrees across the two read paths.
# --------------------------------------------------------------------------


def _import(db, fixture, tmp_path, *, linear=True):
    """Parse + import one real template; precompute linear targets unless it's
    the SOCIE matrix (whose per-cell targets are written inline at import)."""
    tree = parse_template(str(fixture))
    jp = tmp_path / f"{tree.template_id}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)
    if linear:
        import_company_targets(db, tid)
    return tid


def _new_run(conn):
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-06-14T00:00:00Z', 'x.pdf', 'running', '2026-06-14T00:00:00Z')"
    )
    conn.commit()
    return int(cur.lastrowid)


def _leaf_by_label(conn, tid, substr):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND kind = 'LEAF' AND lower(canonical_label) LIKE ? ORDER BY render_row",
        (tid, f"%{substr.lower()}%"),
    ).fetchone()
    return r[0] if r else None


def _matrix_by_label(conn, tid, substr, matrix_col):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND lower(replace(canonical_label, '*', '')) LIKE ? AND matrix_col = ? "
        "ORDER BY render_row",
        (tid, f"%{substr.lower()}%", matrix_col),
    ).fetchone()
    return r[0] if r else None


def assert_cross_check_parity_modulo_rollup_sheet(xlsx_result, fact_result):
    """Strict parity, except a comparand ``sheet`` may differ for a
    cross-sheet-rolled concept (face coord vs sub-sheet leaf, gotcha #21).

    Neutralises ONLY the ``sheet`` field of a fact comparand when every other
    field (label/value/role/statement/row) already matches its xlsx twin, then
    delegates to the exact parity assertion — so a divergence in any
    decision-bearing field, or in any comparand field other than this known
    rollup sheet, still fails loudly.
    """
    import copy

    xs = xlsx_result.comparands or []
    fs = fact_result.comparands or []
    assert len(xs) == len(fs), (
        f"comparand count differs: xlsx={xs!r} != facts={fs!r}")
    normalized = copy.deepcopy(fact_result)
    for xc, fc in zip(xs, normalized.comparands):
        if (fc.label == xc.label and fc.role == xc.role
                and fc.statement == xc.statement and fc.row == xc.row
                and nums_equal(fc.value, xc.value)):
            fc.sheet = xc.sheet
    assert_cross_check_parity(xlsx_result, normalized)


@pytest.mark.parametrize("balanced", [True, False])
def test_socf_to_sofp_cash_e2e_parity(tmp_path, balanced):
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    socf_tid = _import(db, SOCF_FIXTURE, tmp_path)
    sofp_tid = _import(db, SOFP_FIXTURE, tmp_path)
    run_id = _new_run(conn)

    socf_cash = _leaf_by_label(
        conn, socf_tid, "cash and cash equivalents at end of period")
    sofp_cash = _leaf_by_label(conn, sofp_tid, "cash and cash equivalents")
    assert socf_cash and sofp_cash, "cash leaves not found in imported templates"

    _seed(conn, run_id, socf_cash, 900.0)
    _seed(conn, run_id, sofp_cash, 900.0 if balanced else 0.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    socf_wb = tmp_path / "SOCF_filled.xlsx"
    shutil.copyfile(SOCF_FIXTURE, socf_wb)
    sofp_wb = tmp_path / "SOFP_filled.xlsx"
    shutil.copyfile(SOFP_FIXTURE, sofp_wb)
    export_run_to_xlsx(str(db), run_id, str(socf_wb), template_id=socf_tid)
    export_run_to_xlsx(str(db), run_id, str(sofp_wb), template_id=sofp_tid)

    paths = {StatementType.SOCF: str(socf_wb), StatementType.SOFP: str(sofp_wb)}
    xlsx = SOCFToSOFPCashCheck().run(paths, tolerance=1.0, filing_level="company")
    ctx = FactsContext(
        conn=conn, run_id=run_id,
        template_ids={StatementType.SOCF: socf_tid, StatementType.SOFP: sofp_tid},
        filing_level="company", filing_standard="mfrs",
    )
    facts = SOCFToSOFPCashCheck().run_facts(ctx, tolerance=1.0)
    conn.close()

    assert xlsx.status == ("passed" if balanced else "failed")
    # Decision-bearing parity is exact (status, message, expected/actual/diff)
    # and so are the comparand VALUES/labels/roles. The one documented
    # divergence: SOFP "cash and cash equivalents" is cross-sheet-rolled
    # (gotcha #21) — the xlsx path reports the FACE coord (SOFP-CuNonCu, where
    # it scanned col A) while the fact path resolves to the editable sub-sheet
    # LEAF (SOFP-Sub-CuNonCu). Comparands are advisory (never affect pass/fail),
    # and the sub-sheet leaf is arguably the better reviewer target. Closing
    # this advisory coord gap is the label_resolver's flagged Phase-1 alias-coord
    # follow-up; until then the e2e gate asserts everything BUT the comparand
    # sheet for this rolled-up leaf, so the real divergence stays visible here
    # rather than hidden.
    assert_cross_check_parity_modulo_rollup_sheet(xlsx, facts)


@pytest.mark.parametrize("balanced", [True, False])
def test_socie_to_sofp_equity_e2e_parity(tmp_path, balanced):
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    socie_tid = _import(db, SOCIE_FIXTURE, tmp_path, linear=False)
    sofp_tid = _import(db, SOFP_FIXTURE, tmp_path)
    run_id = _new_run(conn)

    # SOCIE equity-at-end lives in the Total matrix column X (MFRS); SOFP total
    # equity is a cascade-computed face total (seed a descendant leaf).
    socie_eq = _matrix_by_label(conn, socie_tid, "equity at end of period", "X")
    sofp_total_eq = _uuid_by_label(conn, sofp_tid, "total equity")
    assert socie_eq and sofp_total_eq, "equity concepts not found"
    sofp_eq_leaf = _descendant_leaf(conn, sofp_total_eq)
    assert sofp_eq_leaf, "no SOFP equity leaf"

    _seed(conn, run_id, socie_eq, 5000.0)
    _seed(conn, run_id, sofp_eq_leaf, 5000.0 if balanced else 4000.0)
    conn.commit()
    recompute_after_turn(str(db), run_id)

    socie_wb = tmp_path / "SOCIE_filled.xlsx"
    shutil.copyfile(SOCIE_FIXTURE, socie_wb)
    sofp_wb = tmp_path / "SOFP_filled.xlsx"
    shutil.copyfile(SOFP_FIXTURE, sofp_wb)
    export_run_to_xlsx(str(db), run_id, str(socie_wb), template_id=socie_tid)
    export_run_to_xlsx(str(db), run_id, str(sofp_wb), template_id=sofp_tid)

    paths = {StatementType.SOCIE: str(socie_wb), StatementType.SOFP: str(sofp_wb)}
    xlsx = SOCIEToSOFPEquityCheck().run(
        paths, tolerance=1.0, filing_level="company", filing_standard="mfrs")
    ctx = FactsContext(
        conn=conn, run_id=run_id,
        template_ids={StatementType.SOCIE: socie_tid, StatementType.SOFP: sofp_tid},
        filing_level="company", filing_standard="mfrs",
    )
    facts = SOCIEToSOFPEquityCheck().run_facts(ctx, tolerance=1.0)
    conn.close()

    # The real gate: both read paths agree on the real exported artifact,
    # whether or not the figures balance.
    assert_cross_check_parity(xlsx, facts)
    assert xlsx.status == ("passed" if balanced else "failed")
