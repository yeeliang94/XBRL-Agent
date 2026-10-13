"""The formatter's declared output shape: a table layout, enforced by the provider.

The formatter describes what each table IS (column roles, row roles, lines);
`notes/table_layout.py` validates that against the live note and compiles it.
These tests pin the schema boundary: unknown keys are refused, unset keys are
not serialised, and an empty answer is valid.

Not covered: answer quality on a nested schema. That needs a live formatter
run. `XBRL_NOTES_FORMATTER_STRUCTURED=0` is the way back if it does not.
"""
from __future__ import annotations

import json
import typing

import pytest
from pydantic import ValidationError

from notes import format_patch
from notes.format_schema import NoteLayout, SheetLayout, layout_to_dict


def test_block_alignment_matches_the_validator():
    from notes.format_schema import TextAlign

    assert set(typing.get_args(TextAlign)) == format_patch.TEXT_ALIGN


@pytest.mark.parametrize("note", [
    {"row": 1, "tables": [{"table": 0, "columns": ["label"], "colour": "red"}]},
    {"row": 1, "blocks": [{"target": {"blocks": "all"}, "style": {"underline": True}}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["figures"]}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["label"],
                           "lines": [{"after_row": 1, "from_col": 1, "to_col": 1, "kind": "thick"}]}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["label"], "overrides": [
        {"row": 1, "cell": 1, "vertical_align": "baseline", "reason": "Header"}]}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["label"], "overrides": [
        {"row": 1, "cell": 1, "reason": "No alignment"}]}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["label"], "paragraphs": [
        {"row": 1, "cell": 1, "paragraph": 1, "indent": 9, "reason": "Too deep"}]}]},
    {"row": 1, "tables": [{"table": 0, "columns": ["label"], "paragraphs": [
        {"row": 1, "cell": 1, "paragraph": 1, "reason": "No style"}]}]},
])
def test_unknown_keys_and_values_are_refused(note):
    with pytest.raises(ValidationError):
        NoteLayout.model_validate(note)


def test_unset_keys_are_dropped_not_serialised_as_null():
    layout = SheetLayout.model_validate({"sheet": "S", "format_summary": "t", "notes": [
        {"row": 1, "blocks": [{"target": {"blocks": "all"}, "style": {"indent": "1em"}}]},
    ]})
    dumped = layout_to_dict(layout)
    assert "null" not in json.dumps(dumped)
    assert dumped["notes"][0]["blocks"][0]["style"] == {"indent": "1em"}


def test_an_empty_layout_is_valid():
    """"Nothing needs formatting" is a real answer, never an invented layout."""
    layout = SheetLayout.model_validate({"sheet": "S", "notes": [], "format_summary": "Already correct."})
    assert layout_to_dict(layout)["notes"] == []


def test_formatter_schema_has_no_confidence_or_styling_keys():
    assert "confidence" not in SheetLayout.model_fields
    table = SheetLayout.model_json_schema()["$defs"]["TableLayout"]["properties"]
    assert not {"border_bottom", "text_align", "fill"} & set(table)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_structured_output_is_on_by_default(monkeypatch):
    from notes.formatting_agent import structured_output_enabled

    monkeypatch.delenv("XBRL_NOTES_FORMATTER_STRUCTURED", raising=False)
    assert structured_output_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_structured_output_has_a_kill_switch(monkeypatch, value):
    from notes.formatting_agent import structured_output_enabled

    monkeypatch.setenv("XBRL_NOTES_FORMATTER_STRUCTURED", value)
    assert structured_output_enabled() is False


def test_agent_declares_the_output_type_when_enabled(monkeypatch, tmp_path):
    from pydantic_ai.models.test import TestModel

    from notes.formatting_agent import create_notes_formatter_agent

    monkeypatch.delenv("XBRL_NOTES_FORMATTER_STRUCTURED", raising=False)
    agent, _deps = create_notes_formatter_agent(
        run_id=1, sheet="Notes-Listofnotes", pdf_path="/tmp/no.pdf",
        db_path=str(tmp_path / "x.db"), model=TestModel(),
    )
    assert agent.output_type is SheetLayout
    prompt = agent._system_prompts[0]
    assert "OUTPUT MODE: STRUCTURED" in prompt
    assert "Return JSON only" not in prompt


def test_formatter_json_fallback_gets_only_json_mode_instruction(monkeypatch, tmp_path):
    from pydantic_ai.models.test import TestModel

    from notes.formatting_agent import create_notes_formatter_agent

    monkeypatch.setenv("XBRL_NOTES_FORMATTER_STRUCTURED", "0")
    agent, _deps = create_notes_formatter_agent(
        run_id=1, sheet="Notes-Listofnotes", pdf_path="/tmp/no.pdf",
        db_path=str(tmp_path / "x.db"), model=TestModel(),
    )
    prompt = agent._system_prompts[0]
    assert "OUTPUT MODE: JSON FALLBACK" in prompt
    assert "OUTPUT MODE: STRUCTURED" not in prompt


def test_a_validated_patch_is_converted_to_json_for_the_screening_path():
    """`str()` of a model is a Python repr; the gates and the retry prompts
    that quote the answer back all work on JSON text."""
    from notes.formatting_agent import _output_json

    patch = SheetLayout.model_validate({
        "sheet": "Notes-CI", "notes": [], "format_summary": "t",
    })
    assert json.loads(_output_json(patch))["sheet"] == "Notes-CI"
    # Free-form output (kill switch on) still passes through untouched.
    assert _output_json('{"sheet": "Notes-CI"}') == '{"sheet": "Notes-CI"}'
