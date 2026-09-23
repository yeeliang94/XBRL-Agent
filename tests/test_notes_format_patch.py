from __future__ import annotations

import importlib
import json
import re

import pytest

from notes.format_patch import FormatPatchError, apply_sheet_patch
from notes.html_sanitize import sanitize_notes_html


def test_prompt_instructs_amount_column_header_and_figure_alignment():
    # Amount headers and figures must share right alignment even when the source
    # differs, without applying that rule to the description column.
    from pathlib import Path
    prompt = (Path(__file__).resolve().parents[1]
              / "prompts" / "notes_formatter.md").read_text(encoding="utf-8")
    assert "currency-caption cell" in prompt
    assert "RM'000" in prompt
    assert "text_align" in prompt
    assert 'Right-align all amount-column headers and figures with `text_align: "right"`' in prompt
    assert "column names, year/period headers" in prompt
    assert "even when" in prompt
    assert "source PDF shows those headers centred or left-aligned" in prompt
    assert "row targets restricted to the amount columns" in prompt
    assert "Keep description and row-label columns left-aligned" in prompt
    assert "takes precedence over source alignment" in prompt


def test_prompt_pins_the_standardized_mtool_profile():
    from pathlib import Path
    prompt = (Path(__file__).resolve().parents[1]
              / "prompts" / "notes_formatter.md").read_text(encoding="utf-8")
    assert "STANDARDISED MTOOL PROFILE" in prompt
    assert "Do not copy decorative brand colours" in prompt
    assert "no borders" in prompt
    assert "header_fill" in prompt


def test_formatter_request_budget_stays_below_pydantic_cap(monkeypatch):
    """The per-click request budget must stay under pydantic-ai's silent 50
    (gotcha #18), including operator overrides which are clamped."""
    import notes.formatting_agent as fa

    assert fa.MAX_FORMATTER_REQUESTS < 50

    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MAX_REQUESTS", "999")
    reloaded = importlib.reload(fa)
    try:
        assert reloaded.MAX_FORMATTER_REQUESTS <= reloaded._MAX_REQUESTS_CEILING < 50
    finally:
        monkeypatch.delenv("XBRL_NOTES_FORMATTER_MAX_REQUESTS", raising=False)
        importlib.reload(fa)


def test_applies_one_coloured_top_border_to_one_cell():
    html = "<table><tr><td>A</td><td>1</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 2}},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#666666",
                    },
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert "border-top: 1px solid #666666" in out.rows[1]
    assert "A" in out.rows[1] and ">1<" in out.rows[1]


def test_removes_all_borders_using_hidden():
    html = (
        '<table><tr><td style="border: 1px solid #000000">A</td>'
        '<td style="border: 1px solid #000000">1</td></tr></table>'
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "all"},
                "style": {"clear_border": ["top", "right", "bottom", "left"]},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    # A full four-edge clear is one shorthand per cell, not four repeated
    # longhands. This preserves the same collapsed-border result and keeps
    # large formatter-authored tables below Excel's cell-size ceiling.
    assert out.rows[1].count("hidden") == 2


def test_total_rows_can_get_single_and_double_rules():
    html = (
        "<table><tr><td>Revenue</td><td>10</td></tr>"
        "<tr><td>Total</td><td>10</td></tr></table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "total_rows"},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#000000",
                    },
                    "border_bottom": {
                        "width": "3px", "style": "double", "color": "#000000",
                    },
                    "text_align": "right",
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert "border-top: 1px solid #000000" in out.rows[1]
    assert "border-bottom: 3px double #000000" in out.rows[1]
    assert "text-align: right" in out.rows[1]


def test_interior_rules_survive_border_collapse_after_clear():
    """Regression: with `border-collapse: collapse`, a `hidden` from clear_border
    (highest collapse priority) killed any interior rule added afterwards, so only
    the table's outer edges showed. Reconciliation must mirror each interior rule
    onto BOTH shared sides so the neighbour's `hidden` can't drop it."""
    html = (
        "<table>"
        "<tr><td>Item</td><td>Amount</td></tr>"
        "<tr><td>Total</td><td>100</td></tr>"
        "<tr><td>Cash</td><td>40</td></tr>"
        "</table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [
                {"target": {"table": 0, "range": "all"},
                 "style": {"clear_border": ["top", "right", "bottom", "left"]}},
                {"target": {"table": 0, "range": "total_rows"}, "style": {
                    "border_top": {"width": "1px", "style": "solid",
                                   "color": "#000000"},
                    "border_bottom": {"width": "3px", "style": "double",
                                      "color": "#000000"},
                }},
            ],
        }],
    }
    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    # Header row's shared edge with the Total row now carries the Total's solid
    # top rule (not the leftover `hidden`), so the collapse renders it.
    assert 'border-bottom: 1px solid #000000' in cells[0]  # Item
    assert 'border-bottom: 1px solid #000000' in cells[1]  # Amount
    # The Total row keeps its own top + bottom rules.
    assert 'border-top: 1px solid #000000' in cells[2]     # Total
    assert 'border-bottom: 3px double #000000' in cells[2]
    # The interior double line reaches the row below's shared top edge.
    assert 'border-top: 3px double #000000' in cells[4]    # Cash
    assert 'border-top: 3px double #000000' in cells[5]


def test_clear_border_on_one_interior_edge_is_not_resurrected():
    """A patch that clears ONLY one side of a shared interior edge must clear it,
    not have the neighbour's still-visible border win it back (Codex review P2).
    The clear mirrors `hidden` onto BOTH sides so the collapsed edge disappears."""
    grid = "1px solid #000000"
    html = (
        "<table>"
        f'<tr><td style="border: {grid}">A</td></tr>'
        f'<tr><td style="border: {grid}">B</td></tr>'
        "</table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {"clear_border": ["bottom"]},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    # The cleared side AND the neighbour's shared side both go hidden — so the
    # interior edge truly disappears in the collapsed-border table.
    assert "border-bottom: 1px hidden #000000" in cells[0]  # A (cleared side)
    assert "border-top: 1px hidden #000000" in cells[1]     # B (shared side)


def test_mirrored_clear_removes_later_longhand_over_matching_shorthand():
    """A matching border shorthand does not make a later side declaration
    redundant: the longhand wins until the mirror removes it."""
    html = "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>"
    patch = {"cells": [{"row": 1, "operations": [
        {"target": {"table": 0, "range": "all"},
         "style": {"clear_border": ["top", "right", "bottom", "left"]}},
        {"target": {"table": 0, "cell": {"r": 2, "c": 1}},
         "style": {"border_top": {
             "width": "1px", "style": "solid", "color": "#000000"}}},
        {"target": {"table": 0, "cell": {"r": 1, "c": 1}},
         "style": {"clear_border": ["bottom"]}},
    ]}]}

    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    assert "border: 1px hidden #000000" in cells[1]
    assert "border-top:" not in cells[1]


def test_later_clear_overrides_earlier_paint_on_shared_edge():
    """Op order is respected: a paint then a later clear of the same edge leaves
    it cleared (the last op to touch an edge wins on BOTH sides)."""
    html = (
        "<table>"
        "<tr><td>A</td></tr>"
        "<tr><td>B</td></tr>"
        "</table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [
                {"target": {"table": 0, "range": "all"}, "style": {
                    "border_bottom": {"width": "1px", "style": "solid",
                                      "color": "#000000"},
                    "border_top": {"width": "1px", "style": "solid",
                                   "color": "#000000"},
                }},
                {"target": {"table": 0, "cell": {"r": 1, "c": 1}},
                 "style": {"clear_border": ["bottom"]}},
            ],
        }],
    }
    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    assert "border-bottom: 1px hidden #000000" in cells[0]  # A cleared last
    assert "border-top: 1px hidden #000000" in cells[1]     # B shared side too


def test_visible_rule_wins_over_neighbours_default_grid():
    """A single-cell rule with no prior clear must still win its shared edge: it
    propagates to the neighbour's opposite side so it doesn't lose the collapse
    tie to the neighbour's themed default grid."""
    html = (
        "<table>"
        "<tr><td>A</td><td>1</td></tr>"
        "<tr><td>B</td><td>2</td></tr>"
        "</table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 2, "c": 1}},
                "style": {"border_top": {"width": "1px", "style": "solid",
                                         "color": "#666666"}},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    assert 'border-top: 1px solid #666666' in cells[2]      # B (targeted)
    assert 'border-bottom: 1px solid #666666' in cells[0]   # A (shared edge)


def test_cols_filter_restricts_row_targets_to_amount_columns():
    """`cols` on total_rows/rows styles only those 1-based cells — the
    accountant pattern where summation rules underline the amounts, not the
    label column."""
    html = (
        "<table>"
        "<tr><td>Revenue</td><td>10</td><td>20</td></tr>"
        "<tr><td>Total</td><td>10</td><td>20</td></tr>"
        "</table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "total_rows", "cols": [2, 3]},
                "style": {
                    "border_bottom": {"width": "3px", "style": "double",
                                      "color": "#000000"},
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch).rows[1]
    cells = re.findall(r"<td[^>]*>[^<]*</td>", out)
    assert "border-bottom" not in cells[3]          # Total label — untouched
    assert "border-bottom: 3px double" in cells[4]  # amount col 2
    assert "border-bottom: 3px double" in cells[5]  # amount col 3


def test_cols_filter_validates_shape():
    html = "<table><tr><td>Total</td><td>10</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "total_rows", "cols": [0]},
                "style": {"text_align": "right"},
            }],
        }],
    }
    with pytest.raises(FormatPatchError, match="cols"):
        apply_sheet_patch({1: html}, patch)


def test_rejects_text_changes_after_sanitize():
    html = "<table><tr><td>A</td></tr></table>"
    # Force an unsupported target by changing table shape through raw malformed
    # patch is not possible; verify the backend rejects unknown style instead.
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {"font_size": "20px"},
            }],
        }],
    }
    with pytest.raises(FormatPatchError, match="unsupported style key"):
        apply_sheet_patch({1: html}, patch)


def test_sanitizer_preserves_formatter_styles():
    html = "<table><tr><td>A</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#666666",
                    },
                    "fill": "header_fill",
                    "text_align": "center",
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    cleaned, warnings = sanitize_notes_html(out.rows[1])
    assert warnings == []
    assert "border-top: 1px solid #666666" in cleaned
    assert "background-color: #f2f2f2" in cleaned
    assert "text-align: center" in cleaned


def test_can_set_table_width_without_structure_change():
    html = "<table><tr><td>A</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "table"},
                "style": {"table_width": "100%"},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert '<table style="width: 100%">' in out.rows[1]


def test_blocks_all_excludes_paragraphs_inside_tables():
    """{"blocks": "all"} styles top-level prose only — a paragraph inside a
    table cell is the cell's content and must not receive block styling."""
    html = "<p>Intro</p><table><tr><td><p>In cell</p></td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"blocks": "all"},
                "style": {"indent": "1em"},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert out.rows[1].count("margin-left: 1em") == 1
    assert '<td><p>In cell</p></td>' in out.rows[1]


# --- Phase 4: padding + paragraph spacing ops -------------------------------

def test_cell_padding_op_applies():
    html = "<table><tr><td>x</td><td>1</td></tr></table>"
    patch = {"cells": [{"row": 1, "operations": [
        {"target": {"table": 0, "cell": {"r": 1, "c": 1}},
         "style": {"padding": "4px 8px"}}]}]}
    out = apply_sheet_patch({1: html}, patch).rows[1]
    assert "padding: 4px 8px" in out


def test_paragraph_spacing_ops_apply():
    html = "<p>Intro</p>"
    patch = {"cells": [{"row": 1, "operations": [
        {"target": {"blocks": "all"},
         "style": {"space_before": "6px", "space_after": "13px"}}]}]}
    out = apply_sheet_patch({1: html}, patch).rows[1]
    assert "margin-top: 6px" in out
    assert "margin-bottom: 13px" in out


def test_malformed_padding_and_spacing_are_rejected():
    html = "<table><tr><td>x</td></tr></table>"
    for style in ({"padding": "4vh"}, {"padding": "1px 2px 3px 4px 5px"},
                  {"space_before": "-6px"}, {"space_after": "6%"}):
        patch = {"cells": [{"row": 1, "operations": [
            {"target": {"table": 0, "cell": {"r": 1, "c": 1}}, "style": style}]}]}
        with pytest.raises(FormatPatchError):
            apply_sheet_patch({1: html}, patch)


# ---------------------------------------------------------------------------
# Write-time compare-and-swap: run_notes_formatter must never clobber a row
# edited during the pass, and must never resurrect a row a regenerate deleted.
# ---------------------------------------------------------------------------

_SHEET = "Notes-Listofnotes"
_TABLE_HTML = "<table><tr><td>Total</td><td>10</td></tr></table>"
_GOOD_PATCH = json.dumps({
    "sheet": _SHEET,
    "cells": [{
        "row": 112,
        "operations": [{
            "target": {"table": 0, "range": "all"},
            "style": {"text_align": "right"},
        }],
    }],
    "format_summary": "Right-aligned numeric columns.",
})


class _FakeResult:
    def __init__(self, output: str, history=()):
        self.output = output
        self.history = list(history)

    def all_messages(self) -> list:
        return [*self.history, f"fake-pass-message: {self.output[:40]}"]


class _FakeAgent:
    """Stands in for the pydantic-ai Agent: returns canned patch JSON and can
    run a side-effect per call (to simulate a concurrent writer mid-pass)."""

    def __init__(self, outputs, on_call=None):
        self._outputs = list(outputs)
        self._on_call = on_call
        self.calls = 0
        self.prompts = []

    async def run(self, prompt, **_kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        if self._on_call:
            self._on_call(self.calls)
        return _FakeResult(self._outputs.pop(0), _kwargs.get("message_history") or [])


@pytest.fixture()
def formatter_db(tmp_path):
    from db import repository as repo
    from db.schema import init_db

    db_path = tmp_path / "audit.sqlite"
    init_db(db_path)
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="s", output_dir=str(tmp_path),
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            label="Disclosure of other notes", html=_TABLE_HTML,
            evidence="Page 3", source_pages=[3],
        )
    return db_path, str(pdf_path), run_id


async def _run_formatter_with_fake_agent(
    monkeypatch, formatter_db, fake_agent, *, style_sources=None,
):
    from pathlib import Path

    import notes.formatting_agent as fa

    db_path, pdf_path, run_id = formatter_db
    monkeypatch.setattr(
        fa, "create_notes_formatter_agent",
        lambda **_kw: (fake_agent, None),
    )
    return await fa.run_notes_formatter(
        run_id=run_id, db_path=str(db_path), pdf_path=pdf_path,
        sheet=_SHEET, model="fake-model",
        output_dir=str(Path(pdf_path).parent),
        style_sources=style_sources,
    )


@pytest.mark.asyncio
async def test_formatter_writes_unedited_rows(monkeypatch, formatter_db):
    from db import repository as repo

    fake = _FakeAgent([_GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert result["skipped_rows"] == []
    db_path, _pdf, run_id = formatter_db
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
        snapshot = repo.fetch_notes_format_snapshots(conn, run_id, _SHEET)
    assert "text-align: right" in cells[0].html
    # The pass snapshotted the pre-format HTML before its first write
    # (schema v27) so "Revert formatting" can restore it.
    assert snapshot == {112: _TABLE_HTML}


@pytest.mark.asyncio
async def test_formatter_attaches_known_pages_to_its_first_model_request(
    monkeypatch, formatter_db,
):
    from pydantic_ai.messages import BinaryContent

    import notes.formatting_agent as fa

    async def fake_preload(_pdf_path, pages):
        assert pages == [3]
        return [
            "=== Page 3 ===",
            BinaryContent(data=b"png", media_type="image/png"),
        ], {3}

    monkeypatch.setattr(fa, "_preload_source_pages", fake_preload)
    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH])

    result = await _run_formatter_with_fake_agent(
        monkeypatch, formatter_db, fake,
    )

    assert result["ok"] is True
    first_request = fake.prompts[0]
    assert isinstance(first_request, list)
    assert "images [3] are attached below" in first_request[0]
    assert any(isinstance(part, BinaryContent) for part in first_request[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("first_output", ["not JSON", _GOOD_PATCH])
async def test_formatter_followups_retain_source_evidence(
    monkeypatch, formatter_db, first_output,
):
    from pydantic_ai import Agent
    from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel
    import notes.formatting_agent as fa

    async def preload(*_args):
        return [BinaryContent(data=b"source-page", media_type="image/png")], {3}

    seen = []

    def respond(messages, _info):
        content = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
        images = [part for value in content if isinstance(value, list)
                  for part in value if isinstance(part, BinaryContent)]
        seen.append(images)
        return ModelResponse(parts=[TextPart(first_output if len(seen) == 1 else _GOOD_PATCH)])

    monkeypatch.setattr(fa, "_preload_source_pages", preload)
    result = await _run_formatter_with_fake_agent(
        monkeypatch, formatter_db, Agent(FunctionModel(respond)),
    )
    assert result["ok"] is True
    assert len(seen) == (2 if first_output == "not JSON" else 1)
    assert all([image.data for image in images] == [b"source-page"] for images in seen)


@pytest.mark.asyncio
async def test_pdf_auto_format_scope_refuses_source_styled_cells(
    monkeypatch, formatter_db,
):
    from db import repository as repo

    db_path, _pdf, run_id = formatter_db
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            label="Disclosure of other notes", html=_TABLE_HTML,
            evidence="Page 3", source_pages=[3], style_source="source",
        )
    fake = _FakeAgent([_GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(
        monkeypatch, formatter_db, fake,
        style_sources={"unstyled", "floor"},
    )
    assert result["ok"] is False
    assert result["error_type"] == "no_unfinished_rows"
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_retry_formats_unfinished_note_without_restyling_finished_note(
    monkeypatch, formatter_db,
):
    from db import repository as repo
    from notes.auto_format import PDF_FORMAT_CANDIDATE_SOURCES

    db_path, _, run_id = formatter_db
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            label="Finished note", html=_TABLE_HTML,
            evidence="Page 3", source_pages=[3], style_source="formatter",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=113,
            label="Unfinished note", html=_TABLE_HTML,
            evidence="Page 3", source_pages=[3], style_source="unstyled",
        )
    patch = json.loads(_GOOD_PATCH)
    patch["cells"][0]["row"] = 113
    fake = _FakeAgent([json.dumps(patch)])

    result = await _run_formatter_with_fake_agent(
        monkeypatch, formatter_db, fake,
        style_sources=PDF_FORMAT_CANDIDATE_SOURCES,
    )

    assert result["ok"] is True
    assert result["changed_rows"] == 1
    with repo.db_session(db_path) as conn:
        rows = {c.row: c for c in repo.list_notes_cells_for_run(conn, run_id)}
    assert rows[112].html == _TABLE_HTML
    assert rows[113].style_source == "formatter"
    assert "text-align: right" in rows[113].html


@pytest.mark.asyncio
async def test_formatter_accepts_prose_wrapped_json(monkeypatch, formatter_db):
    """A patch wrapped in prose ("Here is the patch: {…}") parses via the
    balanced-object extraction — no retry pass is consumed."""
    wrapped = f"Here is the formatting patch you asked for:\n{_GOOD_PATCH}\nDone."
    fake = _FakeAgent([wrapped])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_formatter_retries_once_with_feedback_on_rejected_output(
    monkeypatch, formatter_db,
):
    """Unparseable first output → ONE retry carrying the rejection reason;
    a good retry completes the pass normally."""
    garbage = "I could not produce a patch in the requested format."
    fake = _FakeAgent([garbage, _GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_formatter_reports_original_error_when_retry_also_fails(
    monkeypatch, formatter_db,
):
    garbage = "no json here"
    fake = _FakeAgent([garbage, "still no json"])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is False
    assert result["error_type"] == "validation_failed"
    assert "invalid JSON" in result["error"]
    assert fake.calls == 2  # exactly one retry — no loop


@pytest.mark.asyncio
async def test_formatter_noop_repair_preserves_original_validation_failure(
    monkeypatch, formatter_db,
):
    """An empty repair after an invalid target is a safe content no-op, but
    it is not evidence that formatting was unnecessary or successful.
    """
    invalid = json.loads(_GOOD_PATCH)
    invalid["cells"][0]["operations"][0]["target"] = {
        "table": 7, "range": "all",
    }
    no_op = {
        "sheet": _SHEET,
        "cells": [],
        "format_summary": "No safe existing target could be selected.",
    }
    fake = _FakeAgent([json.dumps(invalid), json.dumps(no_op)])

    result = await _run_formatter_with_fake_agent(
        monkeypatch, formatter_db, fake,
    )

    assert result["ok"] is False
    assert "degraded" not in result
    assert result["error_type"] == "validation_failed"
    assert "table 7 does not exist" in result["error"]
    assert result["changed_rows"] == 0
    assert fake.calls == 2


def test_output_rejected_prompt_carries_error_and_response():
    import notes.formatting_agent as fa

    prompt = fa._build_output_rejected_prompt(
        _SHEET, "Sure! ```json\nnot-json\n```", "formatter returned invalid JSON: x",
    )
    assert "REJECTION: formatter returned invalid JSON: x" in prompt
    assert "no prose" in prompt
    assert "not-json" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [
    "empty", "invalid", "malformed", "outside_failed_rows", "success",
])
async def test_formatter_preserves_valid_note_when_other_note_repair_is_empty(
    monkeypatch, formatter_db, repair,
):
    """A bad numeric target in another note must not discard cash-note styling."""
    from db import repository as repo

    db_path, _, run_id = formatter_db
    other_html = "<table><tr><td>Licensed banks</td><td>Days</td></tr></table>"
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=113,
            label="Another note", html=other_html,
            evidence="Page 3", source_pages=[3], style_source="unstyled",
        )
    mixed = json.loads(_GOOD_PATCH)
    mixed["cells"].append({
        "row": 113, "operations": [{
            "target": {"table": 0, "range": "numeric_cells"},
            "style": {"text_align": "right"},
        }],
    })
    fixed = {**mixed, "cells": [{
        "row": 113, "operations": [{
            "target": {"table": 0, "range": "all"},
            "style": {"text_align": "right"},
        }],
    }]}
    repair_output = {
        "empty": json.dumps({**mixed, "cells": []}),
        "invalid": json.dumps({**mixed, "cells": mixed["cells"][1:]}),
        "malformed": "not json",
        "outside_failed_rows": _GOOD_PATCH,
        "success": json.dumps(fixed),
    }[repair]
    fake = _FakeAgent([json.dumps(mixed), repair_output])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)

    assert result["changed_rows"] == (2 if repair == "success" else 1)
    assert result["ok"] is (repair == "success")
    if repair != "success":
        assert result["error_type"] == "validation_failed"
        assert result["failed_rows"] == [113]
        assert "not a filled notes cell" not in result["row_errors"][113]
    # The repair prompt contains only the failed note, not accepted decisions.
    assert '"row": 112' not in fake.prompts[1]
    with repo.db_session(db_path) as conn:
        rows = {c.row: c for c in repo.list_notes_cells_for_run(conn, run_id)}
        snapshots = repo.fetch_notes_format_snapshots(conn, run_id, _SHEET)
    assert "text-align: right" in rows[112].html
    assert rows[112].style_source == "formatter"
    assert snapshots[112] == _TABLE_HTML
    if repair == "success":
        assert "text-align: right" in rows[113].html
        assert snapshots[113] == other_html
    else:
        assert rows[113].html == other_html
        assert rows[113].style_source == "unstyled"
        assert 113 not in snapshots


def test_partition_rejects_entire_note_including_duplicate_entries():
    from notes.formatting_agent import _partition_valid_patch

    good = json.loads(_GOOD_PATCH)["cells"][0]
    bad = {"row": 112, "operations": [{
        "target": {"table": 7, "range": "all"},
        "style": {"text_align": "right"},
    }]}
    accepted, rejected, errors = _partition_valid_patch(
        {112: _TABLE_HTML, 113: _TABLE_HTML},
        {"cells": [good, {**good, "row": 113}, bad]},
    )
    assert [cell["row"] for cell in accepted["cells"]] == [113]
    assert rejected["cells"] == [good, bad]
    assert list(errors) == [112]


def test_resolve_notes_table_theme_precedence(monkeypatch, formatter_db):
    """Run override (schema v22 snapshot) wins over the firm default env; env
    wins over nothing; unset or malformed env degrades to the HOUSE style.

    The unset/malformed legs used to resolve to `{}` here while the app resolved
    to the house style — so the agent reasoned about a boxed grey grid over a
    ruled, borderless display. Both now go through
    `notes.table_theme.firm_theme`, the single resolver."""
    from db import repository as repo
    import notes.formatting_agent as fa

    db_path, _pdf, run_id = formatter_db
    from notes.table_theme import HOUSE_NOTES_TABLE_STYLE

    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    assert (fa._resolve_notes_table_theme(str(db_path), run_id)
            == HOUSE_NOTES_TABLE_STYLE)

    monkeypatch.setenv(
        "XBRL_NOTES_TABLE_STYLE", json.dumps({"borderStyle": "none"}),
    )
    assert fa._resolve_notes_table_theme(str(db_path), run_id) == {
        "borderStyle": "none",
    }

    with repo.db_session(db_path) as conn:
        repo.set_run_notes_table_style(
            conn, run_id, {"borderStyle": "double", "headerFill": "transparent"},
        )
    assert fa._resolve_notes_table_theme(str(db_path), run_id) == {
        "borderStyle": "double", "headerFill": "transparent",
    }

    monkeypatch.setenv("XBRL_NOTES_TABLE_STYLE", "{not json")
    with repo.db_session(db_path) as conn:
        repo.set_run_notes_table_style(conn, run_id, None)
    assert (fa._resolve_notes_table_theme(str(db_path), run_id)
            == HOUSE_NOTES_TABLE_STYLE)


@pytest.mark.asyncio
async def test_formatter_accepts_patch_without_confidence(monkeypatch, formatter_db):
    patch = json.loads(_GOOD_PATCH)
    fake = _FakeAgent([json.dumps(patch)])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert "confidence" not in result


@pytest.mark.asyncio
async def test_formatter_rejects_text_underline_with_table_border(
    monkeypatch, formatter_db,
):
    from db import repository as repo

    patch = json.loads(_GOOD_PATCH)
    patch["cells"][0]["operations"] = [{
        "target": {"table": 0, "cell": {"r": 1, "c": 2}},
        "style": {"border_bottom": "hidden", "underline": True},
    }]
    fake = _FakeAgent([json.dumps(patch), json.dumps({**patch, "cells": []})])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is False
    assert result["error_type"] == "validation_failed"
    db_path, _, run_id = formatter_db
    with repo.db_session(db_path) as conn:
        cell = repo.list_notes_cells_for_run(conn, run_id)[0]
    assert cell.html == _TABLE_HTML


@pytest.mark.asyncio
async def test_formatter_writes_trace_on_success_and_failure(monkeypatch, formatter_db):
    """The trace lands after every completed pass — present on success AND
    when a later gate fails (gotcha #6: traces matter most for failures)."""
    from pathlib import Path

    _db, pdf_path, _run_id = formatter_db
    trace = Path(pdf_path).parent / f"notes_format_{_SHEET}_conversation_trace.json"

    fake = _FakeAgent([_GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert trace.exists()
    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert len(payload["messages"]) == 1  # one valid formatting decision

    trace.unlink()
    wrong_sheet = {**json.loads(_GOOD_PATCH), "sheet": "Other"}
    fake = _FakeAgent([json.dumps(wrong_sheet), json.dumps(wrong_sheet)])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is False
    assert trace.exists()  # the completed first pass is already on disk


@pytest.mark.asyncio
async def test_formatter_result_carries_token_fields(monkeypatch, formatter_db):
    fake = _FakeAgent([_GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    for key in ("prompt_tokens", "completion_tokens",
                "cache_read_tokens", "cache_write_tokens"):
        assert key in result
        assert isinstance(result[key], int)


@pytest.mark.asyncio
async def test_formatter_skips_row_edited_during_pass(monkeypatch, formatter_db):
    """A user PATCH landing between launch snapshot and final write wins —
    the formatter skips the row instead of writing stale-but-styled HTML."""
    from db import repository as repo

    db_path, _pdf, run_id = formatter_db

    def edit_mid_pass(call_number: int) -> None:
        if call_number == 1:  # while the initial model request runs
            with repo.db_session(db_path) as conn:
                repo.upsert_notes_cell(
                    conn, run_id=run_id, sheet=_SHEET, row=112,
                    label="Disclosure of other notes",
                    html="<p>user edited</p>",
                    evidence="Page 3", source_pages=[3],
                )

    fake = _FakeAgent([_GOOD_PATCH], on_call=edit_mid_pass)
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 0
    assert result["skipped_rows"] == [112]
    assert "skipped" in result["summary"]
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
        snapshot = repo.fetch_notes_format_snapshots(conn, run_id, _SHEET)
    assert cells[0].html == "<p>user edited</p>"
    # Snapshots cover only rows actually WRITTEN — a fully-skipped pass
    # leaves no snapshot (nothing to revert).
    assert snapshot == {}


def test_cas_update_notes_cell_html_is_statement_atomic(formatter_db):
    """The compare lives in the UPDATE's WHERE clause: a mismatched
    expected_html writes nothing — there is no read-then-write window."""
    from db import repository as repo

    db_path, _pdf, run_id = formatter_db
    with repo.db_session(db_path) as conn:
        assert not repo.cas_update_notes_cell_html(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            expected_html="<p>stale expectation</p>", new_html="<p>x</p>",
        )
        assert not repo.cas_update_notes_cell_html(
            conn, run_id=run_id, sheet=_SHEET, row=999,  # missing row
            expected_html=_TABLE_HTML, new_html="<p>x</p>",
        )
        assert repo.cas_update_notes_cell_html(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            expected_html=_TABLE_HTML, new_html="<p>swapped</p>",
        )
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == "<p>swapped</p>"


@pytest.mark.asyncio
async def test_formatter_never_resurrects_deleted_rows(monkeypatch, formatter_db):
    """A sheet regenerate deletes the rows mid-pass; the formatter must not
    upsert its stale snapshot back (deleted row != changed row — both skip)."""
    from db import repository as repo

    db_path, _pdf, run_id = formatter_db

    def delete_mid_pass(call_number: int) -> None:
        if call_number == 1:
            with repo.db_session(db_path) as conn:
                repo.delete_notes_cells_for_run_sheet(
                    conn, run_id=run_id, sheet=_SHEET,
                )

    fake = _FakeAgent([_GOOD_PATCH], on_call=delete_mid_pass)
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 0
    assert result["skipped_rows"] == [112]
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells == []


def test_bold_is_idempotent_across_repeated_patches():
    """Re-running a bold op must not nest <strong><strong>… (tech debt #2)."""
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {"bold": True},
            }],
        }],
    }
    html = "<table><tr><td>Total</td></tr></table>"
    first = apply_sheet_patch({1: html}, patch).rows[1]
    assert first.count("<strong>") == 1
    # Feed the bolded HTML back through the same patch — a whitespace text node
    # or re-serialisation must not defeat the "already wrapped" guard.
    second = apply_sheet_patch({1: first}, patch).rows[1]
    assert second.count("<strong>") == 1


def test_formatter_agent_and_server_resolve_the_same_firm_theme(monkeypatch):
    """These drifted: the agent fell back to {} while the app fell back to the
    house style, so it reasoned about a boxed grid over a ruled display."""
    import server
    from notes import formatting_agent
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    assert (formatting_agent._resolve_notes_table_theme(":memory:", 1)
            == server._notes_table_style())


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_ok", [True, False])
async def test_formatter_malformed_row_gets_one_repair(monkeypatch, formatter_db, repair_ok):
    invalid = json.loads(_GOOD_PATCH)
    invalid["cells"][0]["row"] = "112"
    fake = _FakeAgent([
        json.dumps(invalid),
        _GOOD_PATCH if repair_ok else json.dumps({**invalid, "cells": []}),
    ])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is repair_ok
    assert result["changed_rows"] == (1 if repair_ok else 0)
    assert fake.calls == 2
    if not repair_ok:
        assert result["error_type"] == "validation_failed"
        assert "confidence" not in result
        assert result["patch"] == invalid
        assert result["summary"]


@pytest.mark.asyncio
async def test_formatter_one_cell_row_repair_receives_only_existing_target(
    monkeypatch, formatter_db,
):
    """Run-128 regression: a one-cell row cannot accept ``cols: [2]``.

    The repair turn receives the closed target catalog and can correct the
    patch to column 1 without weakening deterministic validation.
    """
    from db import repository as repo

    db_path, _, run_id = formatter_db
    one_cell = "<table><tr><td>RM'000</td></tr></table>"
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=112,
            label="Disclosure", html=one_cell,
            evidence="Page 3", source_pages=[3], style_source="unstyled",
        )
    invalid = {
        "sheet": _SHEET,
        "cells": [{"row": 112, "operations": [{
            "target": {"table": 0, "rows": [1], "cols": [2]},
            "style": {"text_align": "right"},
        }]}],
        "format_summary": "Align currency caption",
    }
    corrected = json.loads(json.dumps(invalid))
    corrected["cells"][0]["operations"][0]["target"]["cols"] = [1]
    fake = _FakeAgent([
        json.dumps(invalid), json.dumps(corrected),
    ])

    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)

    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert '"c": 1' in fake.prompts[1]
    assert '"c": 2' not in fake.prompts[1]
    with repo.db_session(db_path) as conn:
        row = next(c for c in repo.list_notes_cells_for_run(conn, run_id) if c.row == 112)
    assert "text-align: right" in row.html


def test_partition_validates_merged_operations():
    from notes.formatting_agent import _partition_valid_patch

    good = json.loads(_GOOD_PATCH)["cells"][0]
    accepted, rejected, errors = _partition_valid_patch(
        {112: _TABLE_HTML}, {"cells": [good, good]},
    )
    assert not errors
    assert not rejected["cells"]
    assert accepted["cells"] == [{**good, "operations": good["operations"] * 2}]
    assert apply_sheet_patch({112: _TABLE_HTML}, accepted).changed_rows == 1


def test_spacing_patch_preserves_and_styles_nested_source_heading_levels():
    from bs4 import BeautifulSoup
    html = "<h2>2. Policies</h2><h4>2.1 <em>Revenue</em></h4><p>Body</p>"
    patch = {"cells": [{"row": 1, "operations": [
        {"target": {"blocks": "all"}, "style": {"space_after": "8px"}},
    ]}]}
    out = apply_sheet_patch({1: html}, patch).rows[1]
    soup = BeautifulSoup(out, "html.parser")
    assert soup.h2.get_text() == "2. Policies"
    assert soup.h4.em.get_text() == "Revenue"
    assert all("margin-bottom: 8px" in el["style"] for el in [soup.h2, soup.h4, soup.p])


def test_format_verification_rejects_heading_and_paragraph_flattening():
    from notes.format_verify import verify_format_only
    assert not verify_format_only("<h2>Policy</h2><p>Text</p>", "<p>Policy</p><p>Text</p>").ok
    assert not verify_format_only("<p>First</p><p>second</p>", "<p>First second</p>").ok
    assert not verify_format_only("<p><strong>Important</strong></p>", "<p>Important</p>").ok


@pytest.mark.parametrize("separator", ["\u00a0", "\u202f", "\u2009"])
def test_format_verification_preserves_meaningful_numeric_spacing(separator):
    from notes.format_verify import verify_format_only
    source = f"<p>Amount: 1{separator}000</p>"
    assert not verify_format_only(source, "<p>Amount: 1 000</p>").ok
    assert not verify_format_only(source, "<p>Amount: 1000</p>").ok
    assert verify_format_only(source, f'<p style="margin-top: 6px">Amount: 1{separator}000</p>').ok


def test_format_verification_accepts_equivalent_nbsp_entity_representation():
    from notes.format_verify import verify_format_only
    assert verify_format_only("<p>1&nbsp;000</p>", "<p>1\u00a0000</p>").ok
    assert not verify_format_only("<p>1 000</p>", "<p>1000</p>").ok


@pytest.mark.parametrize(("before", "after"), [
    ("<p>First. Second.</p><p>Third.</p>", "<p>First.</p><p>Second. Third.</p>"),
    ("<h3>Revenue recognition</h3><p>Policy details.</p>",
     "<h3>Revenue</h3><p>recognition Policy details.</p>"),
    ("<p>First<br>Second</p>", "<p>First Second</p>"),
    ("<p>First<br>Second Third</p>", "<p>First Second<br>Third</p>"),
    ("<table><tr><td>A | B</td><td>C</td></tr></table>",
     "<table><tr><td>A</td><td>B | C</td></tr></table>"),
])
def test_format_verification_binds_text_to_source_structure(before, after):
    from notes.format_verify import verify_format_only
    assert not verify_format_only(before, after).ok


def test_format_verification_allows_wrappers_and_added_emphasis():
    from notes.format_verify import verify_format_only
    before = "<h3>Revenue</h3><p>Recognise <em>earned</em> revenue.</p>"
    after = ('<div><h3 style="margin-top: 8px">Revenue</h3>\n'
             '<p><strong>Recognise <span><em>earned</em></span> revenue.</strong></p></div>')
    assert verify_format_only(before, after).ok
