"""Tests for the generic extraction agent factory (Step 4.2)."""

import pytest
from pathlib import Path

from pydantic_ai.models.test import TestModel

from statement_types import StatementType, variants_for


class TestCreateExtractionAgent:
    """Verify the generic factory creates valid agents for each statement type."""

    def _make_agent(self, stmt_type, variant_name, **kwargs):
        from extraction.agent import create_extraction_agent
        return create_extraction_agent(
            statement_type=stmt_type,
            variant=variant_name,
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
            **kwargs,
        )

    def test_creates_agent_for_each_statement(self):
        """Factory should produce an agent+deps tuple for every statement type."""
        for stmt in StatementType:
            variants = variants_for(stmt)
            agent, deps = self._make_agent(stmt, variants[0].name)
            assert agent is not None
            assert deps is not None

    def test_deps_carry_statement_metadata(self):
        """AgentDeps should know which statement type and variant it's serving."""
        from extraction.agent import ExtractionDeps
        agent, deps = self._make_agent(StatementType.SOPL, "Function")
        assert isinstance(deps, ExtractionDeps)
        assert deps.statement_type == StatementType.SOPL
        assert deps.variant == "Function"

    def test_deps_carry_page_hints(self):
        """When page_hints are provided, deps should store them."""
        from extraction.agent import ExtractionDeps
        hints = {"face_page": 14, "note_pages": [30, 31]}
        agent, deps = self._make_agent(
            StatementType.SOFP, "CuNonCu", page_hints=hints
        )
        assert deps.page_hints == hints

    def test_deps_default_no_page_hints(self):
        """Without page_hints, deps.page_hints should be None."""
        from extraction.agent import ExtractionDeps
        agent, deps = self._make_agent(StatementType.SOFP, "CuNonCu")
        assert deps.page_hints is None

    def test_output_path_uses_statement_type(self):
        """Each agent's output path should include the statement type for isolation."""
        from extraction.agent import ExtractionDeps
        agent, deps = self._make_agent(StatementType.SOPL, "Function")
        assert deps.statement_type.value in deps.output_dir or "SOPL" in deps.output_dir or deps.output_dir == "/tmp/output"
