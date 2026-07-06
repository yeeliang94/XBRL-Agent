"""Handoff item 2 — the notes formatter agent receives the mTool size signals.

Deterministic code owns WHAT FITS (the full → lite → flat → oversize ladder in
mtool/notes_exporter, sized against Excel's 32,767-char limit); the agent owns
WHAT TO DO (split for oversize, simplify styling for flat). These tests pin:

  1. `collect_size_signals` re-derives the exporter's verdicts per cell;
  2. `_build_user_prompt` hands the agent each flagged row with the RIGHT
     remedy wording (and no block at all when nothing is flagged);
  3. the prompt file documents the division of labour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
from notes.formatting_agent import (
    _build_user_prompt,
    collect_size_signals,
)


@dataclass
class _Cell:
    row: int
    label: str
    html: str
    evidence: str = "Page 1"
    source_pages: list = field(default_factory=lambda: [1])


def _normal_cell(row: int = 10) -> _Cell:
    return _Cell(row=row, label="Disclosure of revenue",
                 html="<p>Revenue is recognised on transfer of control.</p>")


def _oversize_cell(row: int = 20) -> _Cell:
    # Too big even UNstyled: the wrapped raw payload exceeds Excel's limit.
    body = "x" * (EXCEL_CELL_CHAR_LIMIT + 100)
    return _Cell(row=row, label="Disclosure of financial instruments",
                 html=f"<p>{body}</p>")


def _flat_cell(row: int = 30) -> _Cell:
    # Raw fits, but decoration pushes it over even at the lite tier: a table
    # with many cells — each cell picks up ~150-200 chars of inline styling.
    rows = "".join(
        f"<tr><td>Item {i} description text</td><td>1,{i:03d}</td></tr>"
        for i in range(400)
    )
    cell = _Cell(row=row, label="Disclosure of related party transactions",
                 html=f"<table><tbody>{rows}</tbody></table>")
    # Sanity-check the construction against the real ladder inputs so the
    # test fails loudly if decoration overhead ever changes enough to move
    # this fixture out of the flat window.
    assert len(wrap_footnote_html(cell.html)) <= EXCEL_CELL_CHAR_LIMIT
    return cell


class TestCollectSizeSignals:
    def test_unflagged_cells_produce_no_signals(self):
        assert collect_size_signals([_normal_cell()]) == []

    def test_oversize_and_flat_are_flagged_with_tiers(self):
        signals = collect_size_signals(
            [_normal_cell(10), _oversize_cell(20), _flat_cell(30)]
        )
        by_row = {s["row"]: s for s in signals}
        assert set(by_row) == {20, 30}
        assert by_row[20]["tier"] == "oversize"
        assert by_row[30]["tier"] == "flat"
        assert by_row[20]["label"] == "Disclosure of financial instruments"

    def test_theme_is_threaded_not_required(self):
        # A malformed / absent theme must never break signal collection —
        # from_theme falls back to the baseline style.
        assert collect_size_signals([_normal_cell()], theme=None) == []
        assert collect_size_signals([_normal_cell()], theme={"bogus": 1}) == []


class TestPromptRendering:
    def test_no_signals_no_block(self):
        prompt = _build_user_prompt("Notes-Listofnotes", [_normal_cell()], [1])
        assert "SIZE SIGNALS" not in prompt

    def test_flagged_rows_carry_the_right_remedy(self):
        cells = [_oversize_cell(20), _flat_cell(30)]
        signals = collect_size_signals(cells)
        prompt = _build_user_prompt("Notes-Listofnotes", cells, [1], signals)
        assert "SIZE SIGNALS" in prompt
        # Oversize row → split-the-content remedy, explicitly NOT styling.
        assert "row 20" in prompt
        assert "split" in prompt.lower()
        assert "cannot fix this with styling" in prompt.lower()
        # Flat row → simplify-styling remedy.
        assert "row 30" in prompt
        assert "SIMPLIFY the styling" in prompt
        # The deterministic counts ride along.
        assert "formatting_dropped=1" in prompt
        assert "oversize=1" in prompt

    def test_sizes_are_declared_settled(self):
        cells = [_oversize_cell(20)]
        prompt = _build_user_prompt(
            "Notes-Listofnotes", cells, [1], collect_size_signals(cells),
        )
        assert "do NOT re-derive" in prompt


def test_prompt_file_documents_division_of_labour():
    body = (
        Path(__file__).resolve().parents[1] / "prompts" / "notes_formatter.md"
    ).read_text(encoding="utf-8")
    assert "SIZE SIGNALS" in body
    assert "OVERSIZE" in body and "FLAT" in body and "LITE" in body
    # The advisory rule: the agent never asserts fit; the deterministic
    # fill re-checks.
    assert "never claim" in body.lower()
    assert "re-checks" in body.lower()
    # Oversize is a content problem — the agent must not style its way out.
    assert "CONTENT problem" in body
