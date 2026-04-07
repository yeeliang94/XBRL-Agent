"""Tests for cross-check DB persistence (Phase 5, Step 5.5)."""
from __future__ import annotations

import os
import tempfile

import pytest

from db.schema import init_db
from db.repository import (
    db_session,
    create_run,
    save_cross_check,
    fetch_cross_checks,
)
from cross_checks.framework import CrossCheckResult


class TestCrossCheckPersistence:
    def test_results_saved_to_db(self, tmp_path):
        """After saving cross-check results, SELECT returns all 5 rows."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        # Simulate a completed run with 5 cross-check results
        results = [
            CrossCheckResult(name="sofp_balance", status="passed",
                             expected=1000.0, actual=1000.0, diff=0.0, tolerance=1.0,
                             message="CY balanced"),
            CrossCheckResult(name="sopl_to_socie_profit", status="passed",
                             expected=250000.0, actual=250000.0, diff=0.0, tolerance=1.0,
                             message="Profit matches"),
            CrossCheckResult(name="soci_to_socie_tci", status="pending",
                             message="SOCI not extracted in this run"),
            CrossCheckResult(name="socie_to_sofp_equity", status="failed",
                             expected=750000.0, actual=800000.0, diff=50000.0, tolerance=1.0,
                             message="Equity mismatch"),
            CrossCheckResult(name="socf_to_sofp_cash", status="not_applicable",
                             message="Direct variant, check skipped"),
        ]

        with db_session(db_path) as conn:
            run_id = create_run(conn, pdf_filename="test.pdf")

            # Persist each result
            for r in results:
                save_cross_check(
                    conn,
                    run_id=run_id,
                    check_name=r.name,
                    status=r.status,
                    expected=r.expected,
                    actual=r.actual,
                    diff=r.diff,
                    tolerance=r.tolerance,
                    message=r.message,
                )

        # Verify all 5 rows are persisted
        with db_session(db_path) as conn:
            checks = fetch_cross_checks(conn, run_id)

        assert len(checks) == 5

        # Verify statuses
        statuses = {c.check_name: c.status for c in checks}
        assert statuses["sofp_balance"] == "passed"
        assert statuses["sopl_to_socie_profit"] == "passed"
        assert statuses["soci_to_socie_tci"] == "pending"
        assert statuses["socie_to_sofp_equity"] == "failed"
        assert statuses["socf_to_sofp_cash"] == "not_applicable"

        # Verify numeric fields on the failed check
        equity_check = next(c for c in checks if c.check_name == "socie_to_sofp_equity")
        assert equity_check.expected == pytest.approx(750000.0)
        assert equity_check.actual == pytest.approx(800000.0)
        assert equity_check.diff == pytest.approx(50000.0)
        assert equity_check.tolerance == pytest.approx(1.0)
        assert "mismatch" in equity_check.message.lower()
