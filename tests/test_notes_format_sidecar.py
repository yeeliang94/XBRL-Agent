"""Formatting sidecar + house-style floor (docs/PLAN-notes-format-sidecar.md).

Phase 1: the deterministic foundation — `apply_cell_operations` (the single
per-cell styling gate) and `house_style_ops` (the zero-LLM accountant-
convention floor).
Phase 2/3: `format_ops` on NotesPayload, the write_notes parse, and the
writer's ops → floor → unstyled fallback chain.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from notes.format_defaults import (
    house_style_enabled,
    house_style_ops,
)
from notes.format_patch import FormatPatchError, apply_cell_operations
from notes.payload import NotesPayload


# A typical 3-column disclosure table: label column + two amount columns,
# year header row, currency-caption row, two value rows, and a total row.
TABLE_HTML = (
    "<table>"
    "<tr><th></th><th>2024</th><th>2023</th></tr>"
    "<tr><th></th><th>RM'000</th><th>RM'000</th></tr>"
    "<tr><td>Revenue</td><td>10,000</td><td>9,500</td></tr>"
    "<tr><td>Other income</td><td>(95)</td><td>-</td></tr>"
    "<tr><td>Total</td><td>9,905</td><td>9,500</td></tr>"
    "</table>"
)
PROSE_HTML = "<p>Revenue is recognised when control transfers.</p>"


# ---------------------------------------------------------------------------
# apply_cell_operations — the single per-cell styling gate
# ---------------------------------------------------------------------------


class TestApplyCellOperations:
    def test_valid_ops_style_the_cell(self):
        ops = [
            {
                "target": {"table": 0, "range": "all"},
                "style": {"clear_border": ["top", "right", "bottom", "left"]},
            },
            {
                "target": {"table": 0, "range": "numeric_cells"},
                "style": {"text_align": "right"},
            },
        ]
        out = apply_cell_operations(PROSE_HTML + TABLE_HTML, ops)
        assert "text-align: right" in out
        assert "hidden" in out  # cleared borders persist as hidden triplets
        # Content untouched.
        assert "Revenue is recognised" in out
        assert "10,000" in out

    def test_empty_or_non_list_ops_raise(self):
        with pytest.raises(FormatPatchError):
            apply_cell_operations(TABLE_HTML, [])
        with pytest.raises(FormatPatchError):
            apply_cell_operations(TABLE_HTML, "not-a-list")  # type: ignore[arg-type]

    def test_missing_target_table_raises(self):
        ops = [{"target": {"table": 3, "range": "all"}, "style": {"bold": True}}]
        with pytest.raises(FormatPatchError, match="table 3 does not exist"):
            apply_cell_operations(TABLE_HTML, ops)

    def test_disallowed_style_key_raises(self):
        ops = [
            {
                "target": {"table": 0, "range": "all"},
                "style": {"font_family": "Comic Sans"},
            }
        ]
        with pytest.raises(FormatPatchError, match="unsupported style key"):
            apply_cell_operations(TABLE_HTML, ops)

    def test_result_round_trips_through_sanitiser(self):
        # A second application over already-styled HTML must not churn —
        # the sanitiser accepted everything the gate emitted.
        ops = [
            {
                "target": {"table": 0, "range": "total_rows", "cols": [2, 3]},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#000000",
                    },
                },
            }
        ]
        once = apply_cell_operations(TABLE_HTML, ops)
        twice = apply_cell_operations(once, ops)
        assert once == twice


# ---------------------------------------------------------------------------
# house_style_ops — the deterministic floor
# ---------------------------------------------------------------------------


class TestHouseStyleOps:
    def test_prose_only_returns_no_ops(self):
        assert house_style_ops(PROSE_HTML) == []
        assert house_style_ops("") == []

    def test_disclosure_table_gets_accountant_convention(self):
        ops = house_style_ops(TABLE_HTML)
        # 1. borderless + no fills across the table
        assert ops[0]["target"] == {"table": 0, "range": "all"}
        assert ops[0]["style"]["clear_border"] == [
            "top", "right", "bottom", "left",
        ]
        assert ops[0]["style"]["fill"] == "transparent"
        # 2. amount columns (2, 3) right-aligned — label column exempt
        align = ops[1]
        assert align["target"]["cols"] == [2, 3]
        assert align["target"]["rows"] == [1, 2, 3, 4, 5]
        assert align["style"] == {"text_align": "right"}
        # 3. summation rules under the amount columns of the total row only
        total = ops[2]
        assert total["target"]["range"] == "total_rows"
        assert total["target"]["cols"] == [2, 3]
        assert total["style"]["border_top"]["style"] == "solid"
        assert total["style"]["border_bottom"]["style"] == "double"

    def test_no_total_row_emits_no_summation_op(self):
        html = (
            "<table>"
            "<tr><td>Revenue</td><td>10,000</td></tr>"
            "<tr><td>Cost</td><td>(4,000)</td></tr>"
            "</table>"
        )
        ops = house_style_ops(html)
        assert len(ops) == 2  # clear + align, no total_rows op
        assert all(op["target"].get("range") != "total_rows" for op in ops)

    def test_text_only_table_gets_borderless_but_no_alignment(self):
        html = (
            "<table>"
            "<tr><td>Director</td><td>Alice Tan</td></tr>"
            "<tr><td>Secretary</td><td>Bob Lim</td></tr>"
            "</table>"
        )
        ops = house_style_ops(html)
        assert len(ops) == 1
        assert ops[0]["style"]["clear_border"]

    def test_floor_ops_pass_the_apply_gate(self):
        # The floor must never synthesize something apply_cell_operations
        # rejects — that would silently strand cells unstyled.
        styled = apply_cell_operations(
            PROSE_HTML + TABLE_HTML, house_style_ops(PROSE_HTML + TABLE_HTML)
        )
        assert "text-align: right" in styled
        assert "double" in styled
        assert "Revenue is recognised" in styled

    def test_label_column_exempt_even_when_numeric_looking(self):
        # A year label in column 1 must not drag the label column into the
        # amount set (mirrors shouldRightAlignCell's index-0 exemption).
        html = (
            "<table>"
            "<tr><td>2024</td><td>10,000</td></tr>"
            "<tr><td>2023</td><td>9,500</td></tr>"
            "</table>"
        )
        ops = house_style_ops(html)
        align = [op for op in ops if op["style"].get("text_align")]
        assert align and align[0]["target"]["cols"] == [2]

    def test_kill_switch_reads_env_at_call_time(self, monkeypatch):
        monkeypatch.delenv("XBRL_NOTES_HOUSE_STYLE", raising=False)
        assert house_style_enabled() is True
        monkeypatch.setenv("XBRL_NOTES_HOUSE_STYLE", "0")
        assert house_style_enabled() is False
        monkeypatch.setenv("XBRL_NOTES_HOUSE_STYLE", "1")
        assert house_style_enabled() is True


# ---------------------------------------------------------------------------
# NotesPayload.format_ops — Phase 2 plumbing
# ---------------------------------------------------------------------------


def _payload(**overrides) -> NotesPayload:
    base = dict(
        chosen_row_label="Financial reporting status",
        content="<p>Body.</p>" + TABLE_HTML,
        evidence="Page 14, Note 2(a)",
        source_pages=[14],
        parent_note={"number": "2", "title": "Test Note"},
    )
    base.update(overrides)
    return NotesPayload(**base)


class TestPayloadFormatOps:
    def test_absent_and_valid_ops_accepted(self):
        assert _payload().format_ops is None
        ops = [{"target": {"table": 0, "range": "all"}, "style": {"bold": True}}]
        assert _payload(format_ops=ops).format_ops == ops

    def test_empty_list_collapses_to_none(self):
        assert _payload(format_ops=[]).format_ops is None

    def test_garbage_shapes_rejected(self):
        with pytest.raises(ValueError, match="format_ops"):
            _payload(format_ops="clear all borders")
        with pytest.raises(ValueError, match="format_ops"):
            _payload(format_ops=[{"target": {}}, "not-an-object"])

    def test_numeric_payload_drops_ops(self):
        p = _payload(
            content="",
            numeric_values={"company_cy": 5000},
            format_ops=[
                {"target": {"table": 0, "range": "all"}, "style": {"bold": True}}
            ],
        )
        assert p.format_ops is None  # sheets 13/14 out of formatting scope
        assert p.numeric_values == {"company_cy": 5000.0}


# ---------------------------------------------------------------------------
# Writer integration — Phase 3: ops → floor → unstyled at cell finalisation
# ---------------------------------------------------------------------------

from notes.writer import _combine_format_ops, write_notes_workbook  # noqa: E402
from notes_types import NotesTemplateType, notes_template_path  # noqa: E402

CORP_INFO_SHEET = "Notes-CI"


def _write(tmp_path: Path, payloads: list[NotesPayload]):
    tpl = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    out = tmp_path / "Notes-CI_filled.xlsx"
    return write_notes_workbook(
        template_path=str(tpl),
        payloads=payloads,
        output_path=str(out),
        filing_level="company",
        sheet_name=CORP_INFO_SHEET,
    )


class TestWriterStyling:
    def test_agent_ops_style_the_db_html(self, tmp_path: Path):
        ops = [
            {
                "target": {"table": 0, "range": "numeric_cells"},
                "style": {"text_align": "center"},
            },
        ]
        result = _write(tmp_path, [_payload(format_ops=ops)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        # The agent's observation won (centre) — the floor would have said
        # right — proving ops take precedence over the house style.
        assert "text-align: center" in html
        assert "text-align: right" not in html
        assert "10,000" in html  # content untouched

    def test_invalid_ops_fall_to_floor_with_warning(self, tmp_path: Path):
        bad_ops = [
            {"target": {"table": 7, "range": "all"}, "style": {"bold": True}},
        ]
        result = _write(tmp_path, [_payload(format_ops=bad_ops)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        # Floor styling applied (right-aligned amount columns)…
        assert "text-align: right" in html
        # …and the dropped observation surfaced through the warnings channel.
        assert any("format_ops dropped" in w for w in result.sanitizer_warnings)

    def test_no_ops_gets_floor_styling(self, tmp_path: Path):
        result = _write(tmp_path, [_payload()])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        assert "text-align: right" in html
        assert "double" in html  # total-row summation rule

    def test_flag_off_leaves_html_unstyled(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("XBRL_NOTES_HOUSE_STYLE", "0")
        result = _write(tmp_path, [_payload()])
        assert result.success, result.errors
        assert "style=" not in result.cells_written[0]["html"]

    def test_xlsx_cell_stays_flattened_text(self, tmp_path: Path):
        # Styling is a DB/panel concern — the workbook cell holds the
        # flattened text regardless (gotcha #16: xlsx is a snapshot).
        result = _write(tmp_path, [_payload()])
        wb = openpyxl.load_workbook(result.output_path)
        ws = wb[CORP_INFO_SHEET]
        row = result.cells_written[0]["row"]
        value = ws.cell(row=row, column=2).value
        wb.close()
        assert "style=" not in (value or "")
        assert "10,000" in value


class TestCombineFormatOps:
    def test_reindexes_second_payloads_tables(self, tmp_path: Path):
        # Two payloads on the same row, one table each, each styling ITS OWN
        # table 0 differently. After combining, the second payload's op must
        # land on the combined cell's table 1.
        fill_op = [
            {"target": {"table": 0, "range": "all"}, "style": {"fill": "#f2f2f2"}},
        ]
        centre_op = [
            {
                "target": {"table": 0, "range": "all"},
                "style": {"text_align": "center"},
            },
        ]
        a = _payload(
            content="<p>First.</p><table><tr><td>A</td></tr></table>",
            source_pages=[10],
            format_ops=fill_op,
        )
        b = _payload(
            content="<p>Second.</p><table><tr><td>B</td></tr></table>",
            source_pages=[20],
            format_ops=centre_op,
        )
        result = _write(tmp_path, [a, b])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        # Table A (page 10, sorts first) got the fill; table B the centring.
        a_cell = html.index(">A<")
        b_cell = html.index(">B<")
        fill_pos = html.index("background-color: #f2f2f2")
        centre_pos = html.index("text-align: center")
        assert fill_pos < a_cell < centre_pos < b_cell

    def test_ambiguous_ops_drop_to_floor_for_whole_cell(self):
        blocks_op = [{"target": {"blocks": "all"}, "style": {"bold": True}}]
        table_op = [
            {"target": {"table": 0, "range": "all"}, "style": {"bold": True}},
        ]
        a = _payload(
            content="<p>First.</p><table><tr><td>1,000</td></tr></table>",
            source_pages=[10],
            format_ops=blocks_op,
        )
        b = _payload(
            content="<table><tr><td>2,000</td></tr></table>",
            source_pages=[20],
            format_ops=table_op,
        )
        # A blocks target can't be attributed to one chunk once contents
        # are concatenated — all ops drop, floor takes the whole cell.
        assert _combine_format_ops([a, b]) is None

    def test_single_payload_keeps_its_ops_verbatim(self):
        ops = [{"target": {"table": 0, "range": "all"}, "style": {"bold": True}}]
        p = _payload(format_ops=ops)
        combined = _combine_format_ops([p])
        assert combined == ops


# ---------------------------------------------------------------------------
# Prompt contract — Phase 4: the FORMATTING OBSERVATION section
# ---------------------------------------------------------------------------

from notes.agent import render_notes_prompt  # noqa: E402
from notes_types import NotesTemplateType as _NTT  # noqa: E402


class TestPromptContract:
    @pytest.mark.parametrize("standard", ["mfrs", "mpers"])
    def test_rendered_prompts_carry_format_ops_section(self, standard: str):
        prompt = render_notes_prompt(
            template_type=_NTT.LIST_OF_NOTES,
            filing_level="company",
            inventory=[],
            filing_standard=standard,
        )
        assert "FORMATTING OBSERVATION" in prompt
        assert "format_ops" in prompt
        # The extent rule — the single most common formatting mistake —
        # must survive any base-prompt edit.
        assert "EXTENT" in prompt

    def test_style_free_content_rule_still_present(self):
        # Gotcha #16's invariant: content HTML stays style-free; the
        # sidecar is a separate channel, never a licence for inline styles.
        base = (
            Path(__file__).resolve().parents[1]
            / "prompts" / "_notes_base.md"
        ).read_text(encoding="utf-8")
        assert "style-free" in base
        assert "never" in base.lower()
        # And format_ops is documented as the only formatting channel.
        assert "ONLY formatting" in base or "only formatting" in base

    def test_format_ops_documented_in_output_contract(self):
        base = (
            Path(__file__).resolve().parents[1]
            / "prompts" / "_notes_base.md"
        ).read_text(encoding="utf-8")
        # Field bullet in the OUTPUT CONTRACT list…
        assert "`format_ops` (list, optional)" in base
        # …zero-based-within-payload indexing rule (the combine re-offset
        # depends on agents following it)…
        assert "zero-based within THIS payload" in base
        # …and content-first priority.
        assert "Content always comes first" in base


# ---------------------------------------------------------------------------
# Tool plumbing — write_notes threads raw format_ops into the payload
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _get_tool_function(agent, name: str):
    """Fetch a registered tool's raw function — same toolset-walk pattern
    as test_notes_submit_coverage._agent_tool_names."""
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None or getattr(ts, "tools", None) is None:
            continue
        tool = ts.tools.get(name) if isinstance(ts.tools, dict) else None
        if tool is not None:
            return tool.function
    raise AssertionError(f"tool {name!r} not found on agent")


def test_write_notes_tool_threads_format_ops_into_payload(tmp_path: Path):
    """The agent-tool boundary: a payloads_json carrying format_ops must
    reach the constructed NotesPayload (sink mode captures the payload
    object before any workbook write)."""
    from notes.agent import _ensure_label_index, create_notes_agent

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    agent, deps = create_notes_agent(
        template_type=_NTT.LIST_OF_NOTES,
        pdf_path=str(pdf_path),
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        batch_note_nums=[1],
    )
    deps.payload_sink = []  # sub-agent mode: capture instead of writing
    # Pick a real, exactly-resolvable row label from the live template so
    # the sink's label pre-validation accepts the payload.
    label = _ensure_label_index(deps)[0].original

    ops = [{"target": {"table": 0, "range": "all"}, "style": {"bold": True}}]
    payloads_json = json.dumps({"payloads": [{
        "chosen_row_label": label,
        "content": "<p>Body.</p><table><tr><td>1,000</td></tr></table>",
        "evidence": "Page 3, Note 1",
        "source_pages": [3],
        "parent_note": {"number": "1", "title": "Test"},
        "format_ops": ops,
    }]})

    write_notes = _get_tool_function(agent, "write_notes")
    msg = asyncio.run(write_notes(SimpleNamespace(deps=deps), payloads_json))
    assert "Collected 1 payload" in msg, msg
    assert deps.payload_sink[0].format_ops == ops


# ---------------------------------------------------------------------------
# End-to-end: styled HTML survives the writer → notes_cells chain (Phase 6)
# ---------------------------------------------------------------------------

from db import repository as repo  # noqa: E402
from db.schema import init_db  # noqa: E402
from notes.html_to_text import html_to_excel_text  # noqa: E402
from notes.persistence import persist_notes_cells  # noqa: E402


def test_styled_html_lands_in_notes_cells_and_flattens_clean(tmp_path: Path):
    """The full write-time chain: agent payload with format_ops → writer
    styles the DB html → persist_notes_cells stores it → the Excel
    flattener (the download overlay's text path) still strips styling
    cleanly. This is the sidecar's product contract end to end, minus
    the LLM."""
    ops = [
        {
            "target": {"table": 0, "range": "all"},
            "style": {"clear_border": ["top", "right", "bottom", "left"]},
        },
        {
            "target": {"table": 0, "range": "total_rows", "cols": [2, 3]},
            "style": {
                "border_bottom": {
                    "width": "3px", "style": "double", "color": "#000000",
                },
            },
        },
    ]
    result = _write(tmp_path, [_payload(format_ops=ops)])
    assert result.success, result.errors

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="sess", output_dir=str(tmp_path),
        )
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name=CORP_INFO_SHEET, cells_written=result.cells_written,
    )
    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert len(got) == 1
    stored = got[0].html
    # The agent's observation is in the canonical store…
    assert "3px double" in stored
    assert "hidden" in stored
    # …and the Excel text flattening (what the xlsx download renders for
    # the notes region) is unaffected by the styling.
    flat = html_to_excel_text(stored)
    assert "style=" not in flat
    assert "10,000" in flat
