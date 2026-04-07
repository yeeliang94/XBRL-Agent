"""Tests for page-hint restrictions on sub-agent PDF access (Step 4.3)."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from pydantic_ai.models.test import TestModel

from statement_types import StatementType
from extraction.agent import create_extraction_agent, ExtractionDeps


class TestPageHintsRestriction:
    """When scout provides allowed_pages, view_pdf_pages enforces them."""

    def test_deps_carry_allowed_pages(self):
        """allowed_pages from page_hints should be stored on deps."""
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
            allowed_pages={4, 5, 22, 23},
        )
        assert deps.allowed_pages == {4, 5, 22, 23}

    def test_allowed_pages_none_when_scout_off(self):
        """Without allowed_pages, deps.allowed_pages should be None (unrestricted)."""
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
        )
        assert deps.allowed_pages is None

    def test_page_hints_derive_allowed_pages(self):
        """When page_hints are provided, allowed_pages should be set from them."""
        hints = {"face_page": 14, "note_pages": [30, 31, 32]}
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
            page_hints=hints,
        )
        # When page_hints provided but no explicit allowed_pages,
        # allowed_pages should be auto-derived
        assert deps.allowed_pages is not None
        assert 14 in deps.allowed_pages
        assert 30 in deps.allowed_pages
        assert 31 in deps.allowed_pages
        assert 32 in deps.allowed_pages

    def test_system_prompt_includes_scoped_navigation(self):
        """With page_hints, the prompt should mention the specific pages."""
        hints = {"face_page": 14, "note_pages": [30, 31]}
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
            page_hints=hints,
        )
        # The system prompt should reference page 14
        prompt = agent._system_prompts[0]
        assert "14" in prompt

    def test_system_prompt_includes_self_navigation_when_no_hints(self):
        """Without page_hints, the prompt should instruct self-navigation via TOC."""
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
        )
        prompt = agent._system_prompts[0]
        assert "table of contents" in prompt.lower() or "TOC" in prompt
