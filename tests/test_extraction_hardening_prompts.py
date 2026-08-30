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


# --- Concern 1: intentional cross-sheet dual placements ----------------------

def test_notes_base_intentional_dual_placements():
    flat = _flat("_notes_base.md")
    assert "intentional dual placements" in flat
    assert "share capital" in flat
    assert "issued capital" in flat and "list of notes" in flat
    assert "same prose" in flat
    assert "related-party transactions" in flat
    assert "same disclosure content may appear in both places" in flat


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
    # Reinforced 2026-06-21 (user report: agent still merges year+currency):
    # a NEGATIVE example showing the jammed-into-one-cell form as WRONG, and
    # the year-row-on-top / currency-row-below ordering.
    assert "2024 rm'000" in flat, (
        "_notes_base.md must show the year+currency-in-one-cell form as a "
        "WRONG counter-example"
    )
    assert "year row sits on top" in flat or "year row on top" in flat


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


# --- Run-83 hardening Phase 3 (docs/PLAN-run83-hardening.md Step 5) ---------
# Three proven extraction errors on the IME 2024 run: pledged deposits
# folded into cash equivalents (223 CY / 230 PY), a fair-value movement
# classified by wording instead of source position (204), and a
# many-lines-into-one-row aggregate off by 711 on the receivables row.


def test_socf_pledged_deposits_both_years_rule():
    flat = _flat("socf.md")
    assert "pledged" in flat
    # gotcha #13 pin: prompts never say "restricted"/"allowed" about pages,
    # so the accounting term "restricted cash" stays out of prompt text too
    # (test_prompts asserts the rendered prompt never contains "restricted").
    assert "restricted" not in flat
    assert "both years" in flat
    assert "never the raw sofp balance" in flat


def test_socf_section_by_position_rule():
    flat = _flat("socf.md")
    assert "where the line physically sits in the source statement" in flat
    assert "changes in working capital" in flat
    # The anchor the agent must locate before classifying.
    assert "before or after" in flat


def test_socf_aggregate_arithmetic_rule():
    flat = _flat("socf.md")
    assert "several source lines fold into one template row" in flat
    assert "sum them with the calculator" in flat
    assert "re-check the section subtotal" in flat


def test_sofp_pledged_deposit_rule():
    flat = _flat("sofp.md")
    assert "pledged" in flat
    assert "without being cash equivalents" in flat
    assert "cross-check fails by exactly the pledged amount" in flat


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


# --- Concern #7: strict one-note/one-field routing ----------------------------
# A List-of-Notes disclosure stays complete in one field. Internal topics and
# first-level sub-sections never create additional Sheet-12 placements.

def test_notes_base_requires_one_complete_field():
    flat = _flat("_notes_base.md")
    assert "one top-level pdf note goes to one template field" in flat
    assert "copy the full note into that one field" in flat
    assert "materially different peer topics" in flat
    assert "stay in its one list-of-notes field" in flat
    assert "never break it apart" in flat


def test_notes_base_grouping_is_default_not_optional():
    flat = _flat("_notes_base.md")
    assert "all of its sub-notes belong together in one field" in flat
    assert "sub-notes can be grouped" not in flat


def test_notes_base_share_capital_names_both_target_rows():
    flat = _flat("_notes_base.md")
    assert "disclosure of share capital" in flat
    assert "disclosure of classes of share capital" in flat
    # The second List of Notes candidate row is named and excluded.
    assert "disclosure of issued capital" in flat
    assert "do not put a second copy there" in flat


def test_listofnotes_forbids_distribution_across_fields():
    flat = _flat("notes_listofnotes.md")
    assert "one note, one field" in flat
    assert "complete top-level note stays in one field" in flat
    assert "do not distribute" in flat
    assert "catch-all is preferable to fragmenting" in flat


def test_listofnotes_keeps_related_party_dual_placement():
    # No dedicated related-party row exists on the MFRS or MPERS List of
    # Notes template, so the complete note uses the catch-all while the
    # dedicated Related Party Transactions sheet carries its second copy.
    flat = _flat("notes_listofnotes.md")
    assert "related-party note is an intentional dual placement" in flat
    assert "use the catch-all when no specific row exists" in flat
    assert "related party transactions is not a valid skip" in flat


def test_accounting_policies_multi_topic_paragraph_goes_to_one_row():
    flat = _flat("notes_accounting_policies.md")
    assert "single best-fitting row" in flat
    assert "never cut it into sentences" in flat
    assert "relevant sentence" not in flat


def test_notes_reviewer_split_rule_matches_extraction_rule():
    # The reviewer is the backstop for flagged splits and must apply the same
    # strict one-note/one-field rule as extraction.
    flat = _flat("notes_reviewer.md")
    assert "always a routing violation" in flat
    assert "complete in exactly one list-of-notes field" in flat
    assert "never approve a multi-row" in flat


def test_notes_reviewer_preserves_accounting_policy_fan_out():
    flat = _flat("notes_reviewer.md")
    assert "accounting-policy fan-out" in flat
    assert "field-based, not note-based" in flat
    assert "basis of preparation is not a substitute" in flat
    assert "no field-specific content lost" in flat
    assert "preserve the content without raising a flag solely" in flat
