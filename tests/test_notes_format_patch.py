from __future__ import annotations

import importlib
import json
import re

import pytest

from notes.format_patch import FormatPatchError, apply_sheet_patch
from notes.html_sanitize import sanitize_notes_html


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
    assert out.rows[1].count("hidden") == 8


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


def test_describe_effective_appearance_resolves_theme_defaults():
    """The self-check feedback resolves each cell to its RENDERED look —
    theme defaults where unstyled, explicit values, cleared edges/fills."""
    from notes.format_patch import describe_effective_appearance

    html = (
        "<table>"
        '<tr><th style="background-color: transparent">Name</th><th>Amt</th></tr>'
        '<tr><td>Total</td>'
        '<td style="border-bottom: 3px double #000000; '
        'border-top: 1px hidden #000000">10</td></tr>'
        "</table>"
    )
    lines = "\n".join(describe_effective_appearance(html))
    assert "r1c1" in lines and "none (explicitly cleared)" in lines
    assert "theme header grey (default)" in lines          # unstyled th fill
    assert "bottom=3px double #000000" in lines            # explicit rule
    assert "top=no line (cleared)" in lines                # hidden edge
    assert "theme grid (thin grey, default)" in lines      # unstyled edges


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
    "confidence": 0.9,
})


class _FakeResult:
    def __init__(self, output: str):
        self.output = output

    def all_messages(self) -> list:
        return [f"fake-pass-message: {self.output[:40]}"]


class _FakeAgent:
    """Stands in for the pydantic-ai Agent: returns canned patch JSON and can
    run a side-effect per call (to simulate a concurrent writer mid-pass)."""

    def __init__(self, outputs, on_call=None):
        self._outputs = list(outputs)
        self._on_call = on_call
        self.calls = 0

    async def run(self, prompt, **_kwargs):
        self.calls += 1
        if self._on_call:
            self._on_call(self.calls)
        return _FakeResult(self._outputs.pop(0))


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


async def _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake_agent):
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
    )


@pytest.mark.asyncio
async def test_formatter_writes_unedited_rows(monkeypatch, formatter_db):
    from db import repository as repo

    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH])  # initial + self-check
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
async def test_formatter_accepts_prose_wrapped_json(monkeypatch, formatter_db):
    """A patch wrapped in prose ("Here is the patch: {…}") parses via the
    balanced-object extraction — no retry pass is consumed."""
    wrapped = f"Here is the formatting patch you asked for:\n{_GOOD_PATCH}\nDone."
    fake = _FakeAgent([wrapped, _GOOD_PATCH])  # initial + self-check only
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_formatter_retries_once_with_feedback_on_rejected_output(
    monkeypatch, formatter_db,
):
    """Unparseable first output → ONE retry carrying the rejection reason;
    a good retry completes the pass normally."""
    garbage = "I could not produce a patch in the requested format."
    fake = _FakeAgent([garbage, _GOOD_PATCH, _GOOD_PATCH])  # init + retry + self-check
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert result["changed_rows"] == 1
    assert fake.calls == 3


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


def test_output_rejected_prompt_carries_error_and_response():
    import notes.formatting_agent as fa

    prompt = fa._build_output_rejected_prompt(
        _SHEET, "Sure! ```json\nnot-json\n```", "formatter returned invalid JSON: x",
    )
    assert "REJECTION: formatter returned invalid JSON: x" in prompt
    assert "no prose" in prompt
    assert "not-json" in prompt


def test_self_check_prompt_uses_rendered_appearance():
    import notes.formatting_agent as fa

    prompt = fa._build_self_check_prompt(
        _SHEET, {"sheet": _SHEET, "cells": []},
        {112: '<table><tr><td style="border-bottom: 3px double #000000">10'
              "</td></tr></table>"},
    )
    assert "RENDERED APPEARANCE BY ROW" in prompt
    assert "bottom=3px double #000000" in prompt
    assert "EXTENT" in prompt  # directs attention to border span vs the PDF


@pytest.mark.asyncio
async def test_formatter_low_confidence_returns_error_type(monkeypatch, formatter_db):
    low_conf = json.loads(_GOOD_PATCH)
    low_conf["confidence"] = 0.2
    fake = _FakeAgent([json.dumps(low_conf)])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is False
    assert result["error_type"] == "low_confidence"
    assert result["confidence"] == 0.2


@pytest.mark.asyncio
async def test_formatter_writes_trace_on_success_and_failure(monkeypatch, formatter_db):
    """The trace lands after every completed pass — present on success AND
    when a later gate fails (gotcha #6: traces matter most for failures)."""
    from pathlib import Path

    _db, pdf_path, _run_id = formatter_db
    trace = Path(pdf_path).parent / f"notes_format_{_SHEET}_conversation_trace.json"

    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is True
    assert trace.exists()
    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert len(payload["messages"]) == 2  # initial + self-check pass

    trace.unlink()
    low_conf = json.loads(_GOOD_PATCH)
    low_conf["confidence"] = 0.1
    fake = _FakeAgent([json.dumps(low_conf)])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    assert result["ok"] is False
    assert trace.exists()  # the completed first pass is already on disk


@pytest.mark.asyncio
async def test_formatter_result_carries_token_fields(monkeypatch, formatter_db):
    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH])
    result = await _run_formatter_with_fake_agent(monkeypatch, formatter_db, fake)
    for key in ("prompt_tokens", "completion_tokens",
                "cache_read_tokens", "cache_write_tokens"):
        assert key in result
        assert isinstance(result[key], int)


def test_min_confidence_resolver_validates_and_clamps(monkeypatch):
    import notes.formatting_agent as fa

    monkeypatch.delenv("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", raising=False)
    assert fa._resolve_min_confidence() == 0.70
    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", "not-a-number")
    assert fa._resolve_min_confidence() == 0.70
    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", "1.5")
    assert fa._resolve_min_confidence() == 1.0
    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", "-0.3")
    assert fa._resolve_min_confidence() == 0.0
    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", "0.85")
    assert fa._resolve_min_confidence() == 0.85


@pytest.mark.asyncio
async def test_formatter_skips_row_edited_during_pass(monkeypatch, formatter_db):
    """A user PATCH landing between launch snapshot and final write wins —
    the formatter skips the row instead of writing stale-but-styled HTML."""
    from db import repository as repo

    db_path, _pdf, run_id = formatter_db

    def edit_mid_pass(call_number: int) -> None:
        if call_number == 2:  # during the self-check pass
            with repo.db_session(db_path) as conn:
                repo.upsert_notes_cell(
                    conn, run_id=run_id, sheet=_SHEET, row=112,
                    label="Disclosure of other notes",
                    html="<p>user edited</p>",
                    evidence="Page 3", source_pages=[3],
                )

    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH], on_call=edit_mid_pass)
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
        if call_number == 2:
            with repo.db_session(db_path) as conn:
                repo.delete_notes_cells_for_run_sheet(
                    conn, run_id=run_id, sheet=_SHEET,
                )

    fake = _FakeAgent([_GOOD_PATCH, _GOOD_PATCH], on_call=delete_mid_pass)
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
