"""Formatting sidecar (docs/PLAN-notes-format-sidecar.md).

Phase 1: the deterministic foundation — `apply_cell_operations` (the single
per-cell styling gate).
Phase 2/3: `format_ops` on NotesPayload, the write_notes parse, and the
writer's ops → unstyled fallback chain.

The deterministic house-style floor that used to sit between "ops" and
"unstyled" was REMOVED (2026-07-07) — it imposed an accountant convention
(inventing total-row double-underlines) instead of mirroring the source
PDF. A cell with no usable agent observation now always renders plain;
the notes formatter agent is the on-demand restyle path. Legacy DB rows
may still carry ``style_source = "floor"``.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

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
# Floor removal — the module must stay gone
# ---------------------------------------------------------------------------


def test_house_style_floor_module_removed():
    """The floor was removed 2026-07-07 (it invented borders the source
    didn't show). Reintroducing the module without revisiting that
    decision should fail loudly here."""
    with pytest.raises(ImportError):
        import notes.format_defaults  # noqa: F401


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
# Writer integration — Phase 3: ops → unstyled at cell finalisation
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
        # The agent's observation is applied verbatim — nothing rewrites it.
        assert "text-align: center" in html
        assert "text-align: right" not in html
        assert "10,000" in html  # content untouched

    def test_invalid_ops_surface_warning_and_dont_block(self, tmp_path: Path):
        # Invalid ops are dropped, the cell renders UNSTYLED, and the drop
        # surfaces through the warnings channel — a bad observation never
        # rejects the content write.
        bad_ops = [
            {"target": {"table": 7, "range": "all"}, "style": {"bold": True}},
        ]
        result = _write(tmp_path, [_payload(format_ops=bad_ops)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        assert "style=" not in html  # dropped → plain
        assert any("format_ops dropped" in w for w in result.sanitizer_warnings)
        assert result.cells_written[0]["style_source"] == "unstyled"

    def test_source_styled_table_survives_the_full_write_path(self, tmp_path: Path):
        """END-TO-END through write_notes_workbook, not just _style_cell_html.

        Code review 2026-07-19 caught a KeyError here that every unit test
        missed: `style_counts` was seeded with only {"ops", "unstyled"}, so the
        FIRST cell carrying copied Word markup crashed the writer — and
        write_notes_workbook runs inside the write_notes agent tool, so the
        notes agent died the moment verbatim passthrough actually worked. Any
        new provenance value must be exercised through this path.
        """
        source_table = (
            '<table><tr>'
            '<td style="padding: 1px 5px; text-align: right">10,000</td>'
            '</tr></table>'
        )
        result = _write(
            tmp_path, [_payload(content="<p>Body.</p>" + source_table)]
        )
        assert result.success, result.errors
        cell = result.cells_written[0]
        assert cell["style_source"] == "source"
        assert "padding: 1px 5px" in cell["html"]
        assert "text-align: right" in cell["html"]

    def test_no_ops_default_unstyled(self, tmp_path: Path):
        # No ops → plain, tagged "unstyled". No floor exists to fall to.
        result = _write(tmp_path, [_payload()])
        assert result.success, result.errors
        cell = result.cells_written[0]
        assert "style=" not in cell["html"]
        assert cell["style_source"] == "unstyled"

    def test_legacy_house_style_env_has_no_effect(self, tmp_path: Path, monkeypatch):
        # The old XBRL_NOTES_HOUSE_STYLE kill switch is dead: setting it
        # must NOT resurrect floor styling (the module is deleted).
        monkeypatch.setenv("XBRL_NOTES_HOUSE_STYLE", "1")
        result = _write(tmp_path, [_payload()])
        assert result.success, result.errors
        assert "style=" not in result.cells_written[0]["html"]
        assert result.cells_written[0]["style_source"] == "unstyled"

    def test_agent_ops_tagged_ops_style_source(self, tmp_path: Path):
        ops = [
            {"target": {"table": 0, "range": "numeric_cells"},
             "style": {"text_align": "center"}},
        ]
        result = _write(tmp_path, [_payload(format_ops=ops)])
        assert result.success, result.errors
        assert result.cells_written[0]["style_source"] == "ops"

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

    def test_ambiguous_ops_drop_for_whole_cell(self):
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
        # are concatenated — all ops drop, the cell renders plain.
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

    def test_prompt_expects_observation_for_visible_tables(self):
        # 2026-07-07 rebalance: with the floor removed, the prompt must
        # carry the counterweight ("recording a visible table's formatting
        # is EXPECTED") or cautious models omit format_ops on every payload
        # and whole runs land 100% unstyled (the run-63 Windows incident).
        base = (
            Path(__file__).resolve().parents[1]
            / "prompts" / "_notes_base.md"
        ).read_text(encoding="utf-8")
        flat = " ".join(base.split())  # collapse line-wraps before matching
        assert "EXPECTED, not extra credit" in flat
        # The old blanket escape hatch must stay narrowed to genuine
        # unreadability, not free-floating "when unsure".
        assert "cannot make out" in flat

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


def _make_sink_agent(tmp_path: Path):
    from notes.agent import create_notes_agent

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
    return agent, deps


def test_write_notes_tool_threads_format_ops_into_payload(tmp_path: Path):
    """The agent-tool boundary: a payloads_json carrying format_ops must
    reach the constructed NotesPayload (sink mode captures the payload
    object before any workbook write)."""
    from notes.agent import _ensure_label_index

    agent, deps = _make_sink_agent(tmp_path)
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


class TestUnstyledTableNudge:
    """The write-time feedback loop (2026-07-07, run-63 fix): a write whose
    table content arrives with no format_ops gets a gentle nudge appended
    to the tool return, so the agent can rewrite the rows with its
    observation while the pages are still in front of it. The nudge never
    changes the message PREFIX ("Collected N payload(s)" / "Wrote N
    row(s)") — the history processors' write-boundary regexes anchor on
    it (test_history_processors)."""

    def test_sink_write_nudges_on_unstyled_table(self, tmp_path: Path):
        from notes.agent import _ensure_label_index

        agent, deps = _make_sink_agent(tmp_path)
        label = _ensure_label_index(deps)[0].original
        payloads_json = json.dumps({"payloads": [{
            "chosen_row_label": label,
            "content": "<p>Body.</p><table><tr><td>1,000</td></tr></table>",
            "evidence": "Page 3, Note 1",
            "source_pages": [3],
            "parent_note": {"number": "1", "title": "Test"},
        }]})
        write_notes = _get_tool_function(agent, "write_notes")
        msg = asyncio.run(write_notes(SimpleNamespace(deps=deps), payloads_json))
        assert msg.startswith("Collected 1 payload")
        assert "without format_ops" in msg
        assert "truly plain" in msg  # the no-invention escape hatch

    def test_sink_write_no_nudge_for_prose_or_styled(self, tmp_path: Path):
        from notes.agent import _ensure_label_index

        agent, deps = _make_sink_agent(tmp_path)
        labels = [e.original for e in _ensure_label_index(deps)[:2]]
        ops = [{"target": {"table": 0, "range": "all"}, "style": {"bold": True}}]
        payloads_json = json.dumps({"payloads": [
            {   # prose only — no table, no nudge
                "chosen_row_label": labels[0],
                "content": "<p>Prose only.</p>",
                "evidence": "Page 3, Note 1",
                "source_pages": [3],
                "parent_note": {"number": "1", "title": "Test"},
            },
            {   # table WITH ops — observed, no nudge
                "chosen_row_label": labels[1],
                "content": "<table><tr><td>1,000</td></tr></table>",
                "evidence": "Page 4, Note 2",
                "source_pages": [4],
                "parent_note": {"number": "2", "title": "Test"},
                "format_ops": ops,
            },
        ]})
        write_notes = _get_tool_function(agent, "write_notes")
        msg = asyncio.run(write_notes(SimpleNamespace(deps=deps), payloads_json))
        assert "without format_ops" not in msg

    def test_workbook_write_nudges_on_unstyled_table(self, tmp_path: Path):
        # Same nudge on the direct (non-fanout) write path, driven by the
        # writer's per-cell style_source verdicts.
        from notes.agent import format_unstyled_table_nudge

        nudge = format_unstyled_table_nudge(2)
        assert "2 table cell(s)" in nudge
        assert "without format_ops" in nudge
        assert "truly plain" in nudge
        assert format_unstyled_table_nudge(0) == ""


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


class TestSinkResendSupersede:
    """The nudge invites "re-send the SAME content plus format_ops". In sink
    mode a plain re-send used to CONCATENATE at the final write
    (_combine_payloads), duplicating the content — so an identical re-send
    must replace the earlier sink entry. Comparison is by RESOLVED template
    row, not raw label (peer-review fix): acceptance is fuzzy, so the first
    send may ride a fuzzy-but-accepted label and the retry the exact one."""

    TABLE = "<p>Body.</p><table><tr><td>1,000</td><td>2,000</td></tr></table>"

    def _payload_json(self, label: str, ops=None) -> str:
        entry = {
            "chosen_row_label": label,
            "content": self.TABLE,
            "evidence": "Page 3, Note 1",
            "source_pages": [3],
            "parent_note": {"number": "1", "title": "Test"},
        }
        if ops is not None:
            entry["format_ops"] = ops
        return json.dumps({"payloads": [entry]})

    def test_fuzzy_then_exact_resend_supersedes(self, tmp_path: Path):
        from notes.agent import _ensure_label_index
        from notes.writer import _resolve_row

        agent, deps = _make_sink_agent(tmp_path)
        entries = _ensure_label_index(deps)
        # The longest template label, minus its last two characters: fuzzy
        # (not exact after normalization) but comfortably above the 0.85
        # acceptance threshold, and it best-matches its own source row.
        exact = max((e.original for e in entries), key=len)
        fuzzy = exact[:-2]
        resolved = _resolve_row(entries, fuzzy)
        assert resolved is not None and resolved[2] < 1.0, (
            "test premise: label must be accepted fuzzily, not exactly"
        )

        write_notes = _get_tool_function(agent, "write_notes")
        msg1 = asyncio.run(
            write_notes(SimpleNamespace(deps=deps), self._payload_json(fuzzy))
        )
        assert "Collected 1 payload" in msg1
        assert "without format_ops" in msg1  # the nudge that triggers a resend

        ops = [{"target": {"table": 0, "range": "numeric_cells"},
                "style": {"text_align": "right"}}]
        msg2 = asyncio.run(
            write_notes(SimpleNamespace(deps=deps), self._payload_json(exact, ops))
        )
        assert "Collected 1 payload" in msg2
        # Same row + identical content → superseded, never duplicated.
        assert len(deps.payload_sink) == 1
        assert deps.payload_sink[0].format_ops == ops
        assert deps.payload_sink[0].chosen_row_label == exact

    def test_whitespace_drifted_resend_still_supersedes(self, tmp_path: Path):
        # The nudge asks a MODEL to re-send the same content plus format_ops;
        # models drift on whitespace / attribute order when reproducing HTML.
        # Comparison is on normalized RENDERED text, so a near-identical resend
        # still supersedes instead of falling through to combine (which would
        # duplicate the note). Regression guard for the run-63 follow-up.
        from notes.agent import _ensure_label_index

        agent, deps = _make_sink_agent(tmp_path)
        label = _ensure_label_index(deps)[0].original
        write_notes = _get_tool_function(agent, "write_notes")

        first = json.dumps({"payloads": [{
            "chosen_row_label": label,
            "content": "<p>Body.</p><table><tr><td>1,000</td></tr></table>",
            "evidence": "Page 3, Note 1",
            "source_pages": [3],
            "parent_note": {"number": "1", "title": "Test"},
        }]})
        # Same rendered content, but reindented / attribute-reordered / extra
        # whitespace — what a model resend realistically looks like.
        drifted = json.dumps({"payloads": [{
            "chosen_row_label": label,
            "content": "<p>Body.</p>\n<table >\n  <tr><td>1,000</td></tr>\n</table>",
            "evidence": "Page 3, Note 1",
            "source_pages": [3],
            "parent_note": {"number": "1", "title": "Test"},
            "format_ops": [{"target": {"table": 0, "range": "numeric_cells"},
                            "style": {"text_align": "right"}}],
        }]})
        asyncio.run(write_notes(SimpleNamespace(deps=deps), first))
        asyncio.run(write_notes(SimpleNamespace(deps=deps), drifted))
        # Superseded, not duplicated — the drifted resend replaced the first.
        assert len(deps.payload_sink) == 1
        assert deps.payload_sink[0].format_ops  # the resend's ops won

    def test_different_content_same_row_still_combines(self, tmp_path: Path):
        from notes.agent import _ensure_label_index

        agent, deps = _make_sink_agent(tmp_path)
        label = _ensure_label_index(deps)[0].original
        write_notes = _get_tool_function(agent, "write_notes")

        first = json.dumps({"payloads": [{
            "chosen_row_label": label,
            "content": "<p>Part one.</p>",
            "evidence": "Page 3, Note 1",
            "source_pages": [3],
            "parent_note": {"number": "1", "title": "Test"},
        }]})
        second = json.dumps({"payloads": [{
            "chosen_row_label": label,
            "content": "<p>Part two.</p>",
            "evidence": "Page 4, Note 1",
            "source_pages": [4],
            "parent_note": {"number": "1", "title": "Test"},
        }]})
        asyncio.run(write_notes(SimpleNamespace(deps=deps), first))
        asyncio.run(write_notes(SimpleNamespace(deps=deps), second))
        # Distinct content on one row keeps the combine semantics.
        assert len(deps.payload_sink) == 2


# --- verbatim source passthrough (2026-07-19) ------------------------------
# When the agent copies a Word table out of read_source_note, the styling is
# the SOURCE document's own and arrives on `content`. The sanitiser's
# table-tag whitelist preserves it, so there is nothing to apply — but the
# provenance must be distinguishable from "unstyled", which signals a cell
# that may want a formatter pass.

_STYLED_TABLE = (
    '<table><tr><td style="padding: 1px 5px; text-align: right">'
    '1,595</td></tr></table>'
)


def test_source_styled_table_is_tagged_source_not_unstyled():
    from notes.writer import _style_cell_html

    warnings: list[str] = []
    html, source = _style_cell_html(_STYLED_TABLE, None, "row", warnings)
    assert source == "source"
    assert "padding: 1px 5px" in html
    assert warnings == []


def test_plain_table_without_styling_stays_unstyled():
    from notes.writer import _style_cell_html

    warnings: list[str] = []
    html, source = _style_cell_html(
        "<table><tr><td>1,595</td></tr></table>", None, "row", warnings
    )
    assert source == "unstyled"


def test_styled_prose_without_a_table_is_not_tagged_source():
    """Only table tags keep inline styles through the sanitiser; prose that
    somehow carries one must not be mislabelled as source-styled."""
    from notes.writer import _style_cell_html

    warnings: list[str] = []
    _html, source = _style_cell_html(
        '<p style="text-align: right">text</p>', None, "row", warnings
    )
    assert source == "unstyled"


def test_format_ops_still_win_over_source_detection():
    """An agent that supplies ops is using the translation path — ops apply
    and the cell is tagged `ops`, not `source`."""
    from notes.writer import _style_cell_html

    warnings: list[str] = []
    ops = [{"target": {"table": 0, "range": "all"}, "style": {"bold": True}}]
    _html, source = _style_cell_html(_STYLED_TABLE, ops, "row", warnings)
    assert source == "ops"


def test_sink_write_does_not_nudge_a_verbatim_copied_table(tmp_path: Path):
    """Code review 2026-07-20 (HIGH): the Sheet-12 sink path counted "table
    without format_ops" by payload alone, so an agent that copied a Word table
    VERBATIM (styling inline, no ops — exactly what the source block asks for)
    was told "your table cells will render unstyled — re-send with format_ops".
    Two channels steering opposite ways on the branch's headline feature; an
    obedient re-send would downgrade source-copied styling to model-described
    ops. A PLAIN table on the same write must still get the nudge."""
    from notes.agent import _ensure_label_index

    agent, deps = _make_sink_agent(tmp_path)
    labels = [e.original for e in _ensure_label_index(deps)]
    write_notes = _get_tool_function(agent, "write_notes")

    styled = ('<table><tr><td style="padding: 1px 5px; text-align: right">'
              "1,595</td></tr></table>")
    msg = asyncio.run(write_notes(SimpleNamespace(deps=deps), json.dumps({
        "payloads": [{
            "chosen_row_label": labels[0], "content": styled,
            "evidence": "Page 3, Note 1",
            "parent_note": {"number": "1", "title": "Test"},
        }],
    })))
    assert "Collected 1 payload" in msg
    assert "without format_ops" not in msg

    plain = "<table><tr><td>1,595</td></tr></table>"
    msg2 = asyncio.run(write_notes(SimpleNamespace(deps=deps), json.dumps({
        "payloads": [{
            "chosen_row_label": labels[1], "content": plain,
            "evidence": "Page 4, Note 2",
            "parent_note": {"number": "2", "title": "Other"},
        }],
    })))
    assert "without format_ops" in msg2  # plain tables keep the run-63 nudge


@pytest.mark.parametrize("styled_td", [
    '<td style="">1,595</td>',                    # empty attribute
    '<td style="position: fixed">1,595</td>',     # property the sanitiser rejects
])
def test_sink_nudge_judges_sanitized_html_not_raw(tmp_path: Path, styled_td: str):
    """Code review 2026-07-20 (round 2): the sink check ran on RAW html, so an
    empty or invalid style= suppressed the nudge — while the writer later
    strips exactly those styles and stores the cell UNSTYLED. The verdict must
    match what the writer will store: these cells land plain, so they get the
    nudge."""
    from notes.agent import _ensure_label_index

    agent, deps = _make_sink_agent(tmp_path)
    label = _ensure_label_index(deps)[0].original
    write_notes = _get_tool_function(agent, "write_notes")

    msg = asyncio.run(write_notes(SimpleNamespace(deps=deps), json.dumps({
        "payloads": [{
            "chosen_row_label": label,
            "content": f"<table><tr>{styled_td}</tr></table>",
            "evidence": "Page 3, Note 1",
            "parent_note": {"number": "1", "title": "Test"},
        }],
    })))
    assert "Collected 1 payload" in msg
    assert "without format_ops" in msg


class TestProseStaysStyleFree:
    """The gotcha #16 narrowing is TABLES ONLY, enforced in code.

    Code review 2026-07-19: the sanitiser also permits text-align/margin on
    p/h3/li, color on span, background-color on mark — and source.html
    deliberately carries paragraph styles. Without an explicit strip, "copy the
    table verbatim" would let prose styling ride along into the DB on the
    model's judgement alone.
    """

    def test_paragraph_styles_are_stripped_from_agent_content(self, tmp_path: Path):
        content = (
            '<p style="margin-left: 2em; text-align: center">Body.</p>'
            '<table><tr>'
            '<td style="padding: 1px 5px">10,000</td>'
            '</tr></table>'
        )
        result = _write(tmp_path, [_payload(content=content)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        assert "margin-left" not in html
        assert "text-align: center" not in html
        # ...while the TABLE cell keeps its source styling.
        assert "padding: 1px 5px" in html

    def test_span_and_mark_colours_are_stripped_from_agent_content(self, tmp_path: Path):
        content = (
            '<p><span style="color: #FF0000">red</span> and '
            '<mark style="background-color: #FFFF00">highlit</mark></p>'
            "<table><tr><td>10,000</td></tr></table>"
        )
        result = _write(tmp_path, [_payload(content=content)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        assert "#FF0000" not in html.upper()
        assert "background-color" not in html
        # Text and structure survive — only the attribute goes.
        assert "red" in html and "highlit" in html

    def test_heading_styles_are_stripped_but_headings_remain(self, tmp_path: Path):
        content = (
            '<h3 style="text-align: center">Note 2</h3>'
            "<table><tr><td>10,000</td></tr></table>"
        )
        result = _write(tmp_path, [_payload(content=content)])
        assert result.success, result.errors
        html = result.cells_written[0]["html"]
        assert "<h3" in html
        assert "text-align" not in html.split("<table")[0]


# --- Option A: the source-styled marker (writer + sanitiser lock-step) -------

def test_verbatim_table_is_marked_source_styled():
    from notes.writer import _style_cell_html
    html, prov = _style_cell_html(
        '<table><tr><td style="padding: 1px 0px">A</td></tr></table>',
        None, "X", [],
    )
    assert prov == "source"
    assert 'data-source-styled="true"' in html


def test_unstyled_table_is_not_marked():
    """The marker suppresses the theme grid, so it must never land on a table
    that legitimately wants house styling (PDF-sourced notes)."""
    from notes.writer import _style_cell_html
    html, prov = _style_cell_html(
        "<table><tr><td>A</td></tr></table>", None, "X", [],
    )
    assert prov == "unstyled"
    assert "data-source-styled" not in html


def test_marker_survives_the_human_edit_round_trip():
    """A human edit re-sanitises the cell. If the marker were stripped there,
    the table would silently revert to the house grid on first save."""
    from notes.html_sanitize import sanitize_notes_html
    from notes.writer import _style_cell_html
    html, _ = _style_cell_html(
        '<table><tr><td style="padding: 1px 0px">A</td></tr></table>',
        None, "X", [],
    )
    again, warnings = sanitize_notes_html(html)
    assert 'data-source-styled="true"' in again
    assert warnings == []


def test_forged_marker_value_is_rejected():
    from notes.html_sanitize import sanitize_notes_html
    out, warnings = sanitize_notes_html(
        '<table data-source-styled="drop-my-grid"><tr><td>A</td></tr></table>'
    )
    assert "data-source-styled" not in out
    assert any("data-source-styled" in w for w in warnings)
