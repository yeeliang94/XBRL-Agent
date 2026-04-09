"""Tests for page hints — scout hints are soft guidance, not hard restrictions.

Page hints from scout (face_page, note_pages) appear in the system prompt to guide
the agent, but they must NOT restrict which PDF pages the agent can view.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from pydantic_ai.models.test import TestModel

from statement_types import StatementType
from extraction.agent import create_extraction_agent, ExtractionDeps


class TestPageHints:
    """Page hints guide the agent but never restrict page access."""

    def test_page_hints_do_not_restrict_pages(self):
        """When page_hints are provided, no allowed_pages attribute should exist."""
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
        # Scout hints are soft guidance — no page restriction mechanism should exist
        assert not hasattr(deps, "allowed_pages"), (
            "allowed_pages should not exist on deps — page hints are guidance, not restrictions"
        )

    def test_no_allowed_pages_attribute_exists(self):
        """ExtractionDeps should never have an allowed_pages attribute."""
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
        )
        assert not hasattr(deps, "allowed_pages")

    def test_no_page_restriction_mechanism(self):
        """The agent factory must not accept an allowed_pages parameter."""
        import inspect
        sig = inspect.signature(create_extraction_agent)
        assert "allowed_pages" not in sig.parameters, (
            "create_extraction_agent should not have an allowed_pages parameter"
        )

    def test_system_prompt_includes_page_hints_as_soft_guidance(self):
        """With page_hints, the prompt should mention pages but NOT restrict access."""
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
        prompt = agent._system_prompts[0]
        assert "14" in prompt
        # Prompt must NOT contain restrictive language that would prevent the agent
        # from viewing pages outside the scout hints
        prompt_lower = prompt.lower()
        assert "restricted" not in prompt_lower, "Prompt should not restrict page access"
        assert "rejected" not in prompt_lower, "Prompt should not threaten rejection"

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
