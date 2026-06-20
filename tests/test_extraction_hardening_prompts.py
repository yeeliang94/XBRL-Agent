"""Pins the 2026-06-20 extraction-hardening prompt changes.

These guard the user-reported failure modes (notes duplication, invented
table wording, year/currency row merging, MPERS SOPL revenue bucketing,
SOCF generic-vs-specific routing, mandatory accounting-policy rows, and
note-splitting). All are prompt-level; each assertion below names the
behaviour it protects so a future prompt edit that drops it fails loudly.
"""
from __future__ import annotations

from pathlib import Path

from prompts import render_prompt
from statement_types import StatementType

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flat(name: str) -> str:
    # Collapse all whitespace so assertions match phrases that the prompt
    # hard-wraps across lines (e.g. "never invent a\n  row").
    text = (_PROMPT_DIR / name).read_text(encoding="utf-8").lower()
    return " ".join(text.split())


# --- Concern 1: share-capital cross-sheet exception --------------------------

def test_notes_base_share_capital_exception():
    flat = _flat("_notes_base.md")
    assert "single exception" in flat and "share capital" in flat
    assert "issued capital" in flat and "list of notes" in flat
    assert "same prose" in flat


# --- Concern #1: no invented wording / no invented Total (notes only) --------

def test_notes_base_forbids_invented_total_rows():
    flat = _flat("_notes_base.md")
    assert "invent nothing" in flat
    assert "total" in flat and "subtotal" in flat
    assert "no total line" in flat


def test_no_invent_total_rule_is_notes_only():
    # Must NOT leak into the face-statement base prompt.
    assert "invent nothing" not in _flat("_base.md")


# --- Concern #2: year vs currency are separate table rows --------------------

def test_notes_base_currency_and_year_are_separate_rows():
    flat = _flat("_notes_base.md")
    assert "one source row is one" in flat or "one source row = one" in flat
    assert "currency" in flat
    assert "do not collapse the currency label and the year" in flat
    # The two-header-row worked example is present.
    assert "rm'000</th><th>rm'000</th>" in flat


# --- Concern #3: MPERS SOPL revenue bucket (MPERS-only, code-injected) -------

def test_mpers_sopl_prompt_carries_revenue_bucket_note():
    prompt = render_prompt(
        StatementType.SOPL, "Function", filing_standard="mpers"
    )
    low = prompt.lower()
    assert "mpers revenue bucket" in low
    assert "other revenue from sale of goods" in low
    assert "other revenue from rendering of services" in low
    assert "principal activity" in low
    # Steer off the wrong targets the agent currently picks.
    assert "fee and commission" in low
    assert '"*total' in low or "*total ..." in low


def test_mfrs_sopl_prompt_has_no_mpers_revenue_note():
    prompt = render_prompt(
        StatementType.SOPL, "Function", filing_standard="mfrs"
    )
    assert "MPERS REVENUE BUCKET" not in prompt


def test_mpers_note_does_not_touch_other_statements():
    prompt = render_prompt(
        StatementType.SOFP, "CuNonCu", filing_standard="mpers"
    )
    assert "MPERS REVENUE BUCKET" not in prompt


def test_sopl_md_stays_coarse_after_injection():
    # The injection is code-side; sopl.md itself must remain unchanged-coarse.
    flat = _flat("sopl.md")
    assert "coarse" in flat
    assert "MPERS REVENUE BUCKET".lower() not in flat


# --- Concern #5 (SOCF): prefer specific row, else keep generic ---------------

def test_socf_prefers_specific_row_generalised():
    flat = _flat("socf.md")
    assert "most specific template row" in flat
    assert "footnote" in flat
    assert "lease-interest row" in flat
    # The MPERS-safe fallback half must be present.
    assert "keep it on the generic line" in flat
    assert "never invent a row" in flat


# --- Concern #5 (mandatory accounting-policy rows) --------------------------

def test_accounting_policies_enforces_star_rows():
    flat = _flat("notes_accounting_policies.md")
    assert "mandatory rows" in flat
    assert "begins with `*`" in flat or "leading `*`" in flat
    assert "do not silently leave it blank" in flat
    assert "save_result" in flat


# --- Concern #6: depreciation stays inside PPE policy -----------------------

def test_notes_base_keeps_depreciation_with_ppe():
    flat = _flat("_notes_base.md")
    assert "depreciation" in flat and "property, plant and equipment" in flat
    assert "do not move it to a separate" in flat


def test_accounting_policies_reinforces_no_subaspect_split():
    flat = _flat("notes_accounting_policies.md")
    assert "depreciated" in flat
    assert "do not split it onto" in flat


# --- Concern #7: profit-before-tax table kept together -----------------------

def test_listofnotes_keeps_profit_before_tax_together():
    flat = _flat("notes_listofnotes.md")
    assert "profit" in flat and "before tax" in flat
    assert "do not scatter its individual line items" in flat
