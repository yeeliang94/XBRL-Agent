"""Tests for Company/Group filing-level support across the pipeline.

Phase 1: template_path() routing to Company/ and Group/ subdirectories.
Phase 2: RunConfig and RunConfigRequest accept filing_level.
"""
from __future__ import annotations

import pytest

from statement_types import StatementType, template_path, TEMPLATE_DIR
from coordinator import RunConfig


# ---------------------------------------------------------------------------
# Phase 1 — template_path() routes to Company/ or Group/ subdirectory
# ---------------------------------------------------------------------------


class TestTemplatePathFilingLevel:
    """template_path() gains a `level` parameter that selects Company or Group."""

    def test_default_level_is_company(self):
        path = template_path(StatementType.SOFP, "CuNonCu")
        assert path.parent.name == "Company"

    def test_explicit_company(self):
        path = template_path(StatementType.SOFP, "CuNonCu", level="company")
        assert path.parent.name == "Company"
        assert path.name == "01-SOFP-CuNonCu.xlsx"

    def test_explicit_group(self):
        path = template_path(StatementType.SOFP, "CuNonCu", level="group")
        assert path.parent.name == "Group"
        assert path.name == "01-SOFP-CuNonCu.xlsx"

    def test_company_path_exists_on_disk(self):
        path = template_path(StatementType.SOFP, "CuNonCu", level="company")
        assert path.exists(), f"Expected {path} to exist"

    def test_group_path_exists_on_disk(self):
        path = template_path(StatementType.SOFP, "CuNonCu", level="group")
        assert path.exists(), f"Expected {path} to exist"

    def test_all_variants_exist_company(self):
        """Every variant with a template_filename resolves to a real file at Company level."""
        from statement_types import VARIANTS
        for (stmt, vname), v in VARIANTS.items():
            if not v.template_filename:
                continue
            path = template_path(stmt, vname, level="company")
            assert path.exists(), f"Missing Company template: {path}"

    def test_all_variants_exist_group(self):
        """Every variant with a template_filename resolves to a real file at Group level."""
        from statement_types import VARIANTS
        for (stmt, vname), v in VARIANTS.items():
            if not v.template_filename:
                continue
            path = template_path(stmt, vname, level="group")
            assert path.exists(), f"Missing Group template: {path}"

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError, match="level"):
            template_path(StatementType.SOFP, "CuNonCu", level="consolidated")

    def test_template_dir_has_company_and_group_subdirs(self):
        assert (TEMPLATE_DIR / "Company").is_dir()
        assert (TEMPLATE_DIR / "Group").is_dir()

    def test_socie_routes_correctly(self):
        """SOCIE (special case) also routes through Company/Group subdirs."""
        co = template_path(StatementType.SOCIE, "Default", level="company")
        gr = template_path(StatementType.SOCIE, "Default", level="group")
        assert co.parent.name == "Company"
        assert gr.parent.name == "Group"
        assert co.name == gr.name == "09-SOCIE.xlsx"


# ---------------------------------------------------------------------------
# Phase 2 — RunConfig and RunConfigRequest accept filing_level
# ---------------------------------------------------------------------------


class TestRunConfigFilingLevel:
    """RunConfig carries filing_level, defaulting to 'company'."""

    def test_default_is_company(self):
        cfg = RunConfig(pdf_path="/tmp/test.pdf", output_dir="/tmp/out")
        assert cfg.filing_level == "company"

    def test_explicit_group(self):
        cfg = RunConfig(
            pdf_path="/tmp/test.pdf",
            output_dir="/tmp/out",
            filing_level="group",
        )
        assert cfg.filing_level == "group"

    def test_explicit_company(self):
        cfg = RunConfig(
            pdf_path="/tmp/test.pdf",
            output_dir="/tmp/out",
            filing_level="company",
        )
        assert cfg.filing_level == "company"


class TestRunConfigRequestFilingLevel:
    """RunConfigRequest (API layer) accepts filing_level."""

    def test_default_is_company(self):
        from server import RunConfigRequest
        req = RunConfigRequest(statements=["SOFP"])
        assert req.filing_level == "company"

    def test_explicit_group(self):
        from server import RunConfigRequest
        req = RunConfigRequest(statements=["SOFP"], filing_level="group")
        assert req.filing_level == "group"

    def test_model_dump_includes_filing_level(self):
        from server import RunConfigRequest
        req = RunConfigRequest(statements=["SOFP"], filing_level="group")
        dumped = req.model_dump()
        assert dumped["filing_level"] == "group"

    def test_invalid_filing_level_rejected(self):
        from pydantic import ValidationError
        from server import RunConfigRequest
        with pytest.raises(ValidationError, match="filing_level"):
            RunConfigRequest(statements=["SOFP"], filing_level="bogus")
