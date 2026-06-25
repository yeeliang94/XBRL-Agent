"""Phase 0.5 (skill-first harness) — reference canonicalization pins.

The ``docs/workflows/*.md`` had drifted from the live contract (the SOCIE
"dividends are negative" line, cell-reference ``field_label`` worked examples,
stale tool-limitation "open questions"). Promoting them verbatim would LOWER
accuracy, so the canonicalized copies under ``prompts/references/`` must AGREE
with the live prompts on the load-bearing sign conventions and addressing mode.

These are environment-independent string checks (no gold required) that stop a
future prompt change — or a re-copy of a raw doc — from silently re-staling a
reference. See docs/PROPOSAL-skill-first-harness.md §8 Phase 0.5 + §9.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from extraction import workflow_reference as wr

_REF_DIR = Path(__file__).resolve().parent.parent / "prompts" / "references"
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_ALL_KEYS = sorted(wr._REFERENCE_FILES)


def _flatten(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def _ref_text(key: str) -> str:
    return (_REF_DIR / wr._REFERENCE_FILES[key]).read_text(encoding="utf-8")


# --- generic canonicalization invariants (all 9 references) ------------------

@pytest.mark.parametrize("key", _ALL_KEYS)
def test_reference_has_canonical_preamble(key):
    flat = _flatten(_ref_text(key))
    assert "on-demand workflow reference" in flat
    # The preamble must frame read_template() as authoritative for live coords.
    assert "read_template()" in _ref_text(key)


@pytest.mark.parametrize("key", _ALL_KEYS)
def test_reference_has_no_cellref_field_labels(key):
    """No worked example may use a cell reference (e.g. "C11") as a field_label —
    that conflates a coordinate with a text label and is the exact SOCIE drift."""
    matches = re.findall(r'"field_label":\s*"[A-Z]{1,2}\d+"', _ref_text(key))
    assert not matches, f"{key} still uses cell-ref field_labels: {matches}"


@pytest.mark.parametrize("key", _ALL_KEYS)
def test_reference_has_no_stale_open_questions_or_tool_limits(key):
    flat = _flatten(_ref_text(key))
    assert "open questions" not in flat, f"{key} still carries a stale Open Questions section"
    for stale in (
        "fill_workbook tool may",
        "may need a coordinate",
        "tool must allow writing",
        "requires a fill_workbook enhancement",
        "formulas in this template are broken",
    ):
        assert stale not in flat, f"{key} still carries stale tool-limitation phrasing: {stale!r}"


# --- SOCIE: addressing + dividend sign agree with the live prompt -----------

def test_socie_reference_uses_explicit_row_col_addressing():
    text = _ref_text("socie-default")
    # The matrix worked example must use explicit row/col, not field_label.
    assert '"row": 11, "col": 3' in text
    assert '"field_label"' not in text


def test_socie_reference_and_prompt_agree_dividends_positive():
    ref = _flatten(_ref_text("socie-default"))
    live = _flatten((_PROMPT_DIR / "socie.md").read_text(encoding="utf-8"))
    # Reference matches the live prompt: dividends POSITIVE, not negative.
    assert "positive magnitudes" in ref
    assert "dividends are negative" not in ref
    # The live prompt is the ground truth this reference is reconciled against.
    assert "dividends paid are entered as positive magnitudes" in live
    # And the reference must explain WHY (the formula subtracts the row).
    assert "subtracts the dividends row" in ref or "formula subtracts" in ref


# --- SOCF: cash-flow sign conventions agree with _base.md -------------------

@pytest.mark.parametrize("key", ["socf-indirect", "socf-direct"])
def test_socf_reference_sign_conventions_present(key):
    flat = _flatten(_ref_text(key))
    # Outflows negative, inflows/add-backs positive — consistent with the
    # _base.md SIGN-CONVENTION TROUBLESHOOTING block.
    assert "positive" in flat and "negative" in flat
    base = _flatten((_PROMPT_DIR / "_base.md").read_text(encoding="utf-8"))
    assert "receipts/inflows positive" in base  # ground-truth anchor


# --- SOPL: expenses positive agree with sopl.md / _base.md ------------------

@pytest.mark.parametrize("key", ["sopl-function", "sopl-nature"])
def test_sopl_reference_keeps_expenses_positive(key):
    flat = _flatten(_ref_text(key))
    assert "expenses are positive" in flat
    base = _flatten((_PROMPT_DIR / "_base.md").read_text(encoding="utf-8"))
    assert "expenses and losses are usually entered as" in base  # ground-truth anchor


# --- every reference is reachable through the loader -------------------------

def test_all_references_resolve_through_the_loader():
    """Each shipped reference is reachable via the loader's deps-keyed resolver
    (guards against a file that exists but no run config can ever load)."""
    from statement_types import StatementType as S

    combos = [
        (S.SOFP, "CuNonCu"), (S.SOFP, "OrderOfLiquidity"),
        (S.SOPL, "Function"), (S.SOPL, "Nature"),
        (S.SOCI, "BeforeTax"), (S.SOCI, "NetOfTax"),
        (S.SOCF, "Indirect"), (S.SOCF, "Direct"),
        (S.SOCIE, "Default"),
    ]
    resolved = {wr.resolve_reference_key(s, v) for s, v in combos}
    assert resolved == set(_ALL_KEYS)
