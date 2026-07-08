"""Pinning tests — the SOCIE↔SOFP equity check recovers closing equity from
the per-component columns when the apex Total (col X) was never materialised.

Run-168 QA finding: the check read the SOCIE "Equity at end of period" TOTAL
column X — a formula apex the extraction agent never writes and the cascade
leaves blank whenever the horizontal reserve-subtotal chain (M→S→T→U→X) has no
numeric children (the common case: a company with only issued capital +
retained earnings and no reserve columns). The check then reported the run as
"equity not found" even though every real per-component closing was present.

The fix is additive: read col X first (unchanged when it resolves), and only
when it's blank fall back to summing the primitive component columns of the
closing row (issued capital + retained earnings + reserves + NCI, blank = 0).
These tests drive the FACT path directly at the exact shape — component
closings present, X absent — because that is what the live failure looked like
in ``run_concept_facts``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from cross_checks.framework import FactsContext
from cross_checks.socie_to_sofp_equity import SOCIEToSOFPEquityCheck
from db.schema import init_db
from statement_types import StatementType

REPO = Path(__file__).resolve().parent.parent
SOFP_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
SOCIE_FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "09-SOCIE.xlsx"


def _import(db, fixture, tmp_path, *, linear=True):
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
        "VALUES ('2026-07-09T00:00:00Z', 'x.pdf', 'running', '2026-07-09T00:00:00Z')"
    )
    conn.commit()
    return int(cur.lastrowid)


def _matrix_uuid(conn, tid, substr, matrix_col):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND lower(replace(canonical_label, '*', '')) LIKE ? AND matrix_col = ? "
        "ORDER BY render_row",
        (tid, f"%{substr.lower()}%", matrix_col),
    ).fetchone()
    return r[0] if r else None


def _uuid_by_label(conn, tid, substr):
    r = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND lower(canonical_label) LIKE ? ORDER BY render_row",
        (tid, f"%{substr.lower()}%"),
    ).fetchone()
    return r[0] if r else None


def _seed(conn, run_id, uuid, value):
    conn.execute(
        "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, updated_at) "
        "VALUES (?, ?, 'CY', 'Company', ?, 'observed', 'pdf', '2026-07-09Z')",
        (run_id, uuid, value),
    )


def _setup(tmp_path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    socie_tid = _import(db, SOCIE_FIXTURE, tmp_path, linear=False)
    sofp_tid = _import(db, SOFP_FIXTURE, tmp_path)
    run_id = _new_run(conn)
    ctx = FactsContext(
        conn=conn, run_id=run_id,
        template_ids={StatementType.SOCIE: socie_tid, StatementType.SOFP: sofp_tid},
        filing_level="company", filing_standard="mfrs",
    )
    return db, conn, run_id, socie_tid, sofp_tid, ctx


def test_recovers_closing_equity_from_components_when_total_col_blank(tmp_path):
    _db, conn, run_id, socie_tid, sofp_tid, ctx = _setup(tmp_path)

    # The exact run-168 shape: seed the per-component CLOSING cells (issued
    # capital in col B, retained earnings in col C) but NOT the apex Total X.
    b_close = _matrix_uuid(conn, socie_tid, "equity at end of period", "B")
    c_close = _matrix_uuid(conn, socie_tid, "equity at end of period", "C")
    assert b_close and c_close, "component closing cells not found in template"
    _seed(conn, run_id, b_close, 1_000_000.0)
    _seed(conn, run_id, c_close, 200_000.0)
    # SOFP total equity resolves to the same 1,200,000 — seed the total node
    # directly (no cascade needed for this deterministic check).
    sofp_total_eq = _uuid_by_label(conn, sofp_tid, "total equity")
    assert sofp_total_eq, "SOFP total equity concept not found"
    _seed(conn, run_id, sofp_total_eq, 1_200_000.0)
    conn.commit()

    result = SOCIEToSOFPEquityCheck().run_facts(ctx, tolerance=1.0)
    conn.close()

    # Before the fix this returned "not found" (X absent); now the component
    # sum reconstructs 1,200,000 and the check ties out.
    assert result.status == "passed", result.message
    assert "not filled in" not in result.message
    # The reconstructed SOCIE side is the sum of the seeded components.
    assert result.expected == pytest.approx(1_200_000.0)


def test_all_blank_components_still_reports_not_found(tmp_path):
    # Guard: the fallback must NOT fabricate a 0. When neither the apex X nor
    # any component cell carries a value, the check stays "not found" (with
    # the plain-English message from the #6 fix), never a spurious 0 == SOFP.
    _db, conn, run_id, socie_tid, sofp_tid, ctx = _setup(tmp_path)
    sofp_total_eq = _uuid_by_label(conn, sofp_tid, "total equity")
    _seed(conn, run_id, sofp_total_eq, 1_200_000.0)
    conn.commit()

    result = SOCIEToSOFPEquityCheck().run_facts(ctx, tolerance=1.0)
    conn.close()

    assert result.status == "failed"
    assert "not filled in" in result.message
    assert "None" not in result.message
