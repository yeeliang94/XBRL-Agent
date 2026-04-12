"""Tests for the cross-check framework (Phase 5, Step 5.1 + 5.3)."""
from __future__ import annotations

import pytest

from statement_types import StatementType
from cross_checks.framework import (
    CrossCheck,
    CrossCheckResult,
    run_all,
)


# --- Fake checks for framework tests ---

class AlwaysPassCheck:
    """A trivial check that always passes — proves the framework calls it."""
    name = "always_pass"
    required_statements = {StatementType.SOFP}

    def applies_to(self, run_config) -> bool:
        return True

    def run(self, workbook_paths, tolerance, filing_level="company") -> CrossCheckResult:
        return CrossCheckResult(
            name=self.name,
            status="passed",
            expected=100.0,
            actual=100.0,
            diff=0.0,
            tolerance=tolerance,
            message="Values match",
        )


class AlwaysFailCheck:
    """A check that always fails — proves failures are reported correctly."""
    name = "always_fail"
    required_statements = {StatementType.SOFP, StatementType.SOPL}

    def applies_to(self, run_config) -> bool:
        return True

    def run(self, workbook_paths, tolerance, filing_level="company") -> CrossCheckResult:
        return CrossCheckResult(
            name=self.name,
            status="failed",
            expected=100.0,
            actual=200.0,
            diff=100.0,
            tolerance=1.0,
            message="Mismatch",
        )


class VariantSpecificCheck:
    """A check that only applies to SOCF Indirect variant."""
    name = "indirect_only"
    required_statements = {StatementType.SOCF}

    def applies_to(self, run_config) -> bool:
        return run_config.get("variants", {}).get(StatementType.SOCF) == "Indirect"

    def run(self, workbook_paths, tolerance, filing_level="company") -> CrossCheckResult:
        return CrossCheckResult(
            name=self.name,
            status="passed",
            expected=0.0,
            actual=0.0,
            diff=0.0,
            tolerance=tolerance,
            message="OK",
        )


# --- Step 5.1: Framework tests ---

class TestCrossCheckFramework:
    def test_framework_runs_all_registered(self):
        """A single fake check that always passes → run_all returns 1 pass, 0 fail."""
        checks = [AlwaysPassCheck()]
        run_config = {"statements_to_run": {StatementType.SOFP}}
        # Provide a dummy path so the workbook-path guard is satisfied
        paths = {StatementType.SOFP: "/tmp/fake.xlsx"}
        results = run_all(checks, workbook_paths=paths, run_config=run_config)

        assert len(results) == 1
        assert results[0].status == "passed"
        assert results[0].name == "always_pass"

    def test_framework_returns_multiple_results(self):
        """Multiple checks produce multiple results in order."""
        checks = [AlwaysPassCheck(), AlwaysFailCheck()]
        run_config = {
            "statements_to_run": {StatementType.SOFP, StatementType.SOPL},
        }
        paths = {StatementType.SOFP: "/tmp/f1.xlsx", StatementType.SOPL: "/tmp/f2.xlsx"}
        results = run_all(checks, workbook_paths=paths, run_config=run_config)

        assert len(results) == 2
        assert results[0].status == "passed"
        assert results[1].status == "failed"

    def test_result_carries_expected_actual_diff(self):
        """CrossCheckResult fields are set correctly by the check."""
        checks = [AlwaysFailCheck()]
        run_config = {
            "statements_to_run": {StatementType.SOFP, StatementType.SOPL},
        }
        paths = {StatementType.SOFP: "/tmp/f1.xlsx", StatementType.SOPL: "/tmp/f2.xlsx"}
        results = run_all(checks, workbook_paths=paths, run_config=run_config)
        r = results[0]

        assert r.expected == 100.0
        assert r.actual == 200.0
        assert r.diff == 100.0
        assert r.message == "Mismatch"


# --- Step 5.3: Variant-aware + missing-statement-aware tests ---

class TestCrossCheckSelection:
    def test_pending_when_statement_not_run(self):
        """If a required statement wasn't extracted, check returns 'pending'."""
        checks = [AlwaysFailCheck()]  # requires SOFP + SOPL
        # Only SOFP was run — SOPL is missing
        run_config = {"statements_to_run": {StatementType.SOFP}}
        results = run_all(checks, workbook_paths={}, run_config=run_config)

        assert len(results) == 1
        assert results[0].status == "pending"
        assert "SOPL" in results[0].message

    def test_not_applicable_when_variant_mismatch(self):
        """A variant-specific check returns 'not_applicable' when variant doesn't match."""
        checks = [VariantSpecificCheck()]
        run_config = {
            "statements_to_run": {StatementType.SOCF},
            "variants": {StatementType.SOCF: "Direct"},  # check wants Indirect
        }
        paths = {StatementType.SOCF: "/tmp/fake.xlsx"}
        results = run_all(checks, workbook_paths=paths, run_config=run_config)

        assert len(results) == 1
        assert results[0].status == "not_applicable"

    def test_variant_match_runs_check(self):
        """When variant matches, the check runs normally."""
        checks = [VariantSpecificCheck()]
        run_config = {
            "statements_to_run": {StatementType.SOCF},
            "variants": {StatementType.SOCF: "Indirect"},
        }
        paths = {StatementType.SOCF: "/tmp/fake.xlsx"}
        results = run_all(checks, workbook_paths=paths, run_config=run_config)

        assert len(results) == 1
        assert results[0].status == "passed"

    def test_socie_missing_triggers_multiple_pending(self):
        """SOCIE is needed by multiple checks — skipping it creates multiple pending."""
        # Two checks both require SOCIE
        class CheckA:
            name = "check_a"
            required_statements = {StatementType.SOPL, StatementType.SOCIE}
            def applies_to(self, rc): return True
            def run(self, wp, tol, filing_level="company"): return CrossCheckResult(
                name=self.name, status="passed", message="OK")

        class CheckB:
            name = "check_b"
            required_statements = {StatementType.SOCI, StatementType.SOCIE}
            def applies_to(self, rc): return True
            def run(self, wp, tol, filing_level="company"): return CrossCheckResult(
                name=self.name, status="passed", message="OK")

        # Run without SOCIE
        run_config = {
            "statements_to_run": {StatementType.SOPL, StatementType.SOCI},
        }
        results = run_all([CheckA(), CheckB()], workbook_paths={}, run_config=run_config)

        assert all(r.status == "pending" for r in results)
        assert all("SOCIE" in r.message for r in results)

    def test_missing_workbook_returns_failed(self):
        """If statement was selected but agent failed (no workbook), check returns 'failed'."""
        checks = [AlwaysPassCheck()]  # requires SOFP
        run_config = {"statements_to_run": {StatementType.SOFP}}
        # SOFP was run but produced no workbook — not in workbook_paths
        results = run_all(checks, workbook_paths={}, run_config=run_config)

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "Workbook missing" in results[0].message

    def test_check_exception_caught_gracefully(self):
        """If a check's .run() raises, it returns failed instead of crashing."""
        class BrokenCheck:
            name = "broken"
            required_statements = {StatementType.SOFP}
            def applies_to(self, rc): return True
            def run(self, wp, tol, filing_level="company"): raise RuntimeError("boom")

        checks = [BrokenCheck()]
        run_config = {"statements_to_run": {StatementType.SOFP}}
        results = run_all(
            checks,
            workbook_paths={StatementType.SOFP: "/tmp/fake.xlsx"},
            run_config=run_config,
        )

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "boom" in results[0].message
