"""The formatter's declared output shape must match what the validator takes.

The formatter returned free-form text that `_parse_json_patch` repaired —
stripping ```json fences, then hunting for the first balanced object when the
model wrapped its answer in prose. That repair existed because the shape was
only ASKED for, in the prompt. `notes/format_schema.py` declares it instead,
so the provider enforces it.

The remaining risk is DRIFT: new validator keys must be represented in the
schema unless deliberately legacy-only, as text underline now is. These tests
hold the AI vocabulary to the validator's supported keys.

Not covered: whether a model answers a nested schema as well as it answers
prose. That needs a live formatter run. `XBRL_NOTES_FORMATTER_STRUCTURED=0` is
the way back if it does not.
"""
from __future__ import annotations

import json
import typing

import pytest

from notes import format_patch
from notes.format_patch import apply_cell_operations
from notes.format_schema import (
    SheetFormatPatch,
    Style,
    Target,
    patch_to_dict,
)

_TABLE = (
    "<table>"
    "<tr><th>Item</th><th>2024</th><th>2023</th></tr>"
    "<tr><td>Widgets</td><td>1,000</td><td>900</td></tr>"
    "<tr><td>Total</td><td>1,000</td><td>900</td></tr>"
    "</table>"
)


def _apply(operations: list[dict]) -> str:
    patch = SheetFormatPatch.model_validate(
        {
            "sheet": "S",
            "cells": [{"row": 1, "operations": operations}],
            "format_summary": "t",
        }
    )
    return apply_cell_operations(
        _TABLE, patch_to_dict(patch)["cells"][0]["operations"]
    )


# ---------------------------------------------------------------------------
# The schema matches the validator except for legacy text underline.
# ---------------------------------------------------------------------------

def test_every_ai_style_key_the_validator_accepts_exists_on_the_schema():
    """Only legacy underline is intentionally excluded from the AI contract."""
    accepted = set(format_patch.STYLE_TO_CSS) | {
        "clear_border", "fill", "text_align", "indent", "padding",
        "space_before", "space_after", "table_width", "bold", "italic",
    }
    missing = accepted - set(Style.model_fields)
    assert not missing, f"schema cannot express style keys: {sorted(missing)}"


def test_every_target_mode_the_validator_accepts_exists_on_the_schema():
    for field in ("table", "range", "cols", "cell", "rows", "blocks"):
        assert field in Target.model_fields, field


def test_border_widths_and_styles_match_the_validator():
    from notes.format_schema import BorderStyle, BorderWidth

    assert set(typing.get_args(BorderWidth)) == format_patch.BORDER_WIDTHS
    assert set(typing.get_args(BorderStyle)) == format_patch.BORDER_STYLES


def test_text_align_matches_the_validator():
    from notes.format_schema import TextAlign

    assert set(typing.get_args(TextAlign)) == format_patch.TEXT_ALIGN


def test_sides_match_the_validator():
    from notes.format_schema import Side

    assert set(typing.get_args(Side)) == set(format_patch.SIDES)


# ---------------------------------------------------------------------------
# End to end: schema output must survive the real validator.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,ops", [
    ("prompt example 1 — borderless with summation rules", [
        {"target": {"table": 0, "range": "all"},
         "style": {"clear_border": ["top", "right", "bottom", "left"]}},
        {"target": {"table": 0, "range": "total_rows", "cols": [2, 3]},
         "style": {"border_top": {"width": "1px", "style": "solid", "color": "#000000"},
                   "border_bottom": {"width": "3px", "style": "double", "color": "#000000"}}},
        {"target": {"table": 0, "range": "numeric_cells"},
         "style": {"text_align": "right"}},
    ]),
    ("prompt example 2 — one coloured top border", [
        {"target": {"table": 0, "cell": {"r": 3, "c": 2}},
         "style": {"border_top": {"width": "1px", "style": "solid", "color": "#666666"}}},
    ]),
    ("bare 'hidden' border literal", [
        {"target": {"table": 0, "range": "header"}, "style": {"border_bottom": "hidden"}},
    ]),
    ("fill + bold + padding", [
        {"target": {"table": 0, "range": "header"},
         "style": {"fill": "header_fill", "bold": True, "padding": "4px 8px"}},
    ]),
    ("rows target + table_width", [
        {"target": {"table": 0, "rows": [1]}, "style": {"italic": True}},
        {"target": {"table": 0, "range": "table"}, "style": {"table_width": "100%"}},
    ]),
])
def test_schema_output_passes_the_validator(name, ops):
    assert _apply(ops), name


def test_unset_keys_are_dropped_not_serialised_as_null():
    """`exclude_none` is load-bearing: `_apply_style` iterates the keys it is
    handed, so a `"border_top": null` would reach `_border_value(None)` and
    raise, and a `"table": null` would defeat the `blocks` branch."""
    patch = SheetFormatPatch.model_validate({
        "sheet": "S",
        "cells": [{"row": 1, "operations": [
            {"target": {"blocks": "all"}, "style": {"indent": "1em"}},
        ]}],
        "format_summary": "t",
    })
    dumped = patch_to_dict(patch)
    assert "null" not in json.dumps(dumped)
    op = dumped["cells"][0]["operations"][0]
    assert op["target"] == {"blocks": "all"}
    assert op["style"] == {"indent": "1em"}


def test_an_empty_patch_is_valid():
    """"Nothing needs restyling" is a real answer and must not be forced into
    an invented operation."""
    patch = SheetFormatPatch.model_validate({
        "sheet": "S", "cells": [], "format_summary": "Already correct.",
    })
    assert patch_to_dict(patch)["cells"] == []


def test_formatter_schema_does_not_request_confidence_or_text_underline():
    assert "confidence" not in SheetFormatPatch.model_fields
    assert "underline" not in Style.model_fields


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
    assert agent.output_type is SheetFormatPatch
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

    patch = SheetFormatPatch.model_validate({
        "sheet": "Notes-CI", "cells": [], "format_summary": "t",
    })
    assert json.loads(_output_json(patch))["sheet"] == "Notes-CI"
    # Free-form output (kill switch on) still passes through untouched.
    assert _output_json('{"sheet": "Notes-CI"}') == '{"sheet": "Notes-CI"}'
