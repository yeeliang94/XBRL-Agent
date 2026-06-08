"""Plan B — the lookup_definitions agent tool (extraction / reviewer / notes).

Pins that the tool is registered on each agent and that the shared JSON wrapper
batches, scopes by standard, and degrades gracefully on bad input.
"""
from __future__ import annotations

import json

import pytest

from concept_model.definitions import lookup_as_json


# --------------------------------------------------------------------------
# Shared JSON wrapper (the impl all three agents call)
# --------------------------------------------------------------------------

def test_wrapper_batches_and_scopes_by_standard() -> None:
    out = json.loads(
        lookup_as_json(["other current non-trade payables", "accruals"], "mfrs")
    )
    assert out["standard"] == "mfrs"
    assert set(out["results"]) == {"other current non-trade payables", "accruals"}
    first = out["results"]["other current non-trade payables"]["matches"][0]
    assert "definition" in first and first["definition"].strip()


def test_wrapper_empty_input_returns_error_not_crash() -> None:
    out = json.loads(lookup_as_json([], "mfrs"))
    assert "error" in out


def test_wrapper_unknown_standard_returns_error_payload() -> None:
    out = json.loads(lookup_as_json(["anything"], "gaap"))
    assert "error" in out


# --------------------------------------------------------------------------
# Tool registration on each agent factory
# --------------------------------------------------------------------------

def _tool_names(agent) -> set[str]:
    """Registered tool names — same introspection the existing extraction-agent
    tests use (iterate ``agent.toolsets`` and collect each toolset's tools)."""
    names: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def test_extraction_agent_exposes_lookup_definitions() -> None:
    from pydantic_ai.models.test import TestModel
    from statement_types import StatementType
    from extraction.agent import create_extraction_agent

    agent, _deps = create_extraction_agent(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        pdf_path="/tmp/test.pdf",
        template_path="/tmp/test.xlsx",
        model=TestModel(),
        output_dir="/tmp/output",
    )
    assert "lookup_definitions" in _tool_names(agent)


def test_notes_agent_exposes_lookup_definitions(tmp_path) -> None:
    from pydantic_ai.models.test import TestModel
    from notes_types import NotesTemplateType
    from notes.agent import create_notes_agent

    agent, _deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path="/tmp/no.pdf",
        inventory=[],
        filing_level="company",
        model=TestModel(),
        output_dir=str(tmp_path),
    )
    assert "lookup_definitions" in _tool_names(agent)
