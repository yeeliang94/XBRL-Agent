"""Pins the run-49 (Amway MPERS Company) post-mortem prompt changes.

The run-49 review surfaced three judgement failures the SOCI code fix
(tests/test_cell_resolver.py) does not address on its own:

- agents writing a text/title row into a numeric value cell (SOCI);
- an extraction agent mapping only ONE finer PDF component into a broader
  template row and dropping the rest (SOFP PPE: office furniture mapped,
  office equipment 807 omitted);
- the reviewer tracing that omitted component but flagging `stuck` instead
  of applying the grounded aggregation, mistaking it for a forbidden plug.

These fixes are deliberately GENERAL — they state the principle, not the
Amway numbers — so they apply to every statement and document. Each
assertion below names the behaviour it protects so a future prompt edit
that drops it fails loudly. The office-equipment figures appear only as an
illustrative evidence example, never as the load-bearing rule.
"""
from __future__ import annotations

from pathlib import Path

from prompts import render_prompt
from statement_types import StatementType

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flat(name: str) -> str:
    text = (_PROMPT_DIR / name).read_text(encoding="utf-8").lower()
    return " ".join(text.split())


# --- #3: no prose in numeric value cells (general, all statements) -----------

def test_base_forbids_prose_in_value_cells():
    flat = _flat("_base.md")
    assert "value cells hold numbers" in flat
    # Names the prose shapes generally, not "SOCI" or one row label.
    assert "statement title" in flat and "section heading" in flat
    assert "cannot be stored as a fact" in flat


def test_value_cell_rule_reaches_rendered_prompt():
    # End-to-end: the base rule composes into a real statement prompt.
    p = render_prompt(StatementType.SOCI, "Default").lower()
    assert "value cells hold numbers" in p


# --- #5: aggregate ALL disclosed components into a broader row, reconcile ----

def test_base_requires_summing_all_components():
    flat = _flat("_base.md")
    assert "sum every disclosed component" in flat
    # The failure mode it guards: mapping only the nearest-named one.
    assert "not just the one whose name looks closest" in flat
    assert "reconcile to the note" in flat


# --- #6: grounded aggregation is NOT a plug (base integrity rule) ------------

def test_base_distinguishes_aggregation_from_plug():
    flat = _flat("_base.md")
    assert "distinguish plugging from legitimate aggregation" in flat
    # The crisp test: source of each addend, not whether arithmetic was used.
    assert "every addend independently on the page" in flat
    # The no-plug guard itself must still be present (gotcha #17 intact).
    assert "never use a catch-all row as a balancing figure" in flat


# --- #6/#7: reviewer applies grounded aggregation, doesn't flag stuck --------

def test_reviewer_has_under_aggregation_pattern():
    flat = _flat("reviewer.md")
    assert "under-aggregation" in flat
    assert "grounded aggregation, not a plug" in flat


def test_reviewer_guard_not_over_applied_to_aggregation():
    flat = _flat("reviewer.md")
    assert "this guard targets plugs, not aggregation" in flat
    assert "an error to fix, not a residual to flag" in flat
    # The plug guard must remain (gotcha #17 not weakened).
    assert "never plug a balancing residual into a catch-all row" in flat
