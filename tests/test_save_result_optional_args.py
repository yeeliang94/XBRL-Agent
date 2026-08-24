"""`save_result()` must validate as the prompts describe calling it.

The prompts describe a bare completion call because facts and notes are
already persisted by their write tools. The completion tools therefore must
not expose redundant JSON-in-a-string arguments, optional or required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _schema_for(agent, tool_name: str) -> dict:
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict) and tool_name in tools:
            tool = tools[tool_name]
            for attr in ("parameters_json_schema", "json_schema"):
                schema = getattr(tool, attr, None)
                if isinstance(schema, dict):
                    return schema
            td = getattr(tool, "tool_def", None)
            schema = getattr(td, "parameters_json_schema", None)
            if isinstance(schema, dict):
                return schema
    raise AssertionError(f"{tool_name} not registered or schema not exposed")


def test_prompts_really_do_describe_a_bare_call():
    """If this stops being true, the fix below is no longer needed."""
    hits = [p.name for p in _PROMPTS.glob("*.md")
            if "save_result()" in p.read_text(encoding="utf-8")]
    assert len(hits) >= 5, hits


def test_face_save_result_takes_no_required_args(tmp_path):
    from pydantic_ai.models.test import TestModel

    from extraction.agent import create_extraction_agent
    from statement_types import StatementType

    agent, _deps = create_extraction_agent(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        pdf_path="/tmp/no.pdf", template_path="/tmp/no.xlsx",
        output_dir=str(tmp_path), model=TestModel(),
    )
    required = _schema_for(agent, "save_result").get("required", [])
    properties = _schema_for(agent, "save_result").get("properties", {})
    assert "fields_json" not in properties
    assert required == [] or set(required) <= {"acknowledge_unresolved"}


def test_notes_save_result_takes_no_required_args(tmp_path):
    from pydantic_ai.models.test import TestModel

    from notes import agent as notes_agent
    from notes_types import NotesTemplateType

    agent, _deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path),
    )
    schema = _schema_for(agent, "save_result")
    assert "payloads_json" not in schema.get("properties", {})
    assert schema.get("required", []) == []


def test_both_save_tools_remove_redundant_stringified_json_arguments():
    import extraction.agent as face_agent
    import notes.agent as notes_agent

    notes_src = Path(notes_agent.__file__).read_text(encoding="utf-8")
    face_src = Path(face_agent.__file__).read_text(encoding="utf-8")

    assert "payloads_json" not in notes_src
    assert "fields_json" not in face_src
