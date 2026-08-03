"""`save_result()` must validate as the prompts describe calling it.

Peer review, 2026-08-01. Eight prompt files instruct the agent to "call
`save_result()`" with no arguments, but both save tools declared a REQUIRED
string — `fields_json` on the face path, `payloads_json` on the notes path.
A model that follows the prompt literally gets a schema validation error and
burns a turn re-sending an argument that changes nothing: by the time
save_result runs, the face values are already in the workbook and in
`run_concept_facts`, and every notes payload has already been persisted by
`write_notes`.

The face tool's BODY already tolerated an empty string (Windows incident, run
35). The required-ness of the parameter is what produced the retry, so the
body fix alone never reached the failure.
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
    assert "fields_json" not in required, (
        "the prompts say `save_result()`; a required fields_json forces a "
        "schema retry on every statement"
    )
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
    required = _schema_for(agent, "save_result").get("required", [])
    assert "payloads_json" not in required


def test_both_save_tools_treat_an_omitted_arg_as_valid_not_as_an_error():
    """A default alone is not enough — the body must not then reject "".

    The face tool already had this branch; the notes tool raised "Invalid
    JSON" on an empty string, so defaulting the parameter without fixing the
    body would have moved the failure rather than removed it.
    """
    import extraction.agent as face_agent
    import notes.agent as notes_agent

    notes_src = Path(notes_agent.__file__).read_text(encoding="utf-8")
    face_src = Path(face_agent.__file__).read_text(encoding="utf-8")

    assert 'payloads_json: str = ""' in notes_src
    assert "if not payloads_json or not payloads_json.strip():" in notes_src
    assert 'fields_json: str = ""' in face_src
    assert "if not fields_json or not fields_json.strip():" in face_src
