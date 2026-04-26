"""RUN-REVIEW P0-1 (2026-04-26): pin the diff-first CORRECTION prompt.

Pre-P0-1 the prompt told the agent that `inspect_workbook` was
"mandatory before sign-sensitive edits". Combined with no iteration
counter in `_run_correction_pass`, this invited a 40-turn inspect-flood
that hit pydantic-ai's silent 50-request cap. The new wording explicitly
caps inspect calls at ≤2 and structures the workflow around a single
planned diff.
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _read_correction_prompt() -> str:
    return (_PROMPTS / "correction.md").read_text(encoding="utf-8")


def test_diff_first_workflow_anchor_present() -> None:
    """The header anchor `=== YOUR WORKFLOW (DIFF-FIRST) ===` is the
    test's stable handle for the diff-first restructure. A future edit
    that reverts to inspect-first must drop this anchor and trip the
    test."""
    text = _read_correction_prompt()
    assert "DIFF-FIRST" in text, (
        "correction.md must carry the named '(DIFF-FIRST)' workflow "
        "header — that's the structural anchor for the new shape"
    )


def test_inspect_call_budget_capped() -> None:
    """The new prompt must cap inspect_workbook at ≤2 — pre-P0-1 wording
    said it was 'mandatory' and the agent burned 40 turns on it."""
    text = _read_correction_prompt().lower()
    assert "at most twice" in text or "≤2" in text or "<=2" in text, (
        "correction.md must impose an explicit ≤2 inspect-call cap"
    )
    # The previous wording must be GONE — pinning its absence catches
    # accidental reverts to the inspect-flood-friendly version.
    assert "is mandatory" not in text, (
        "correction.md must NOT say inspect is 'mandatory' — that wording "
        "invited the 40-turn flood reported in RUN-REVIEW §3.4"
    )


def test_single_fill_workbook_call_required() -> None:
    """The diff-first contract: ONE fill_workbook covering all edits,
    not iterative inspect→fill→inspect→fill loops."""
    text = _read_correction_prompt().lower()
    assert "one `fill_workbook`" in text or "one fill_workbook" in text, (
        "correction.md must specify ONE fill_workbook call carrying "
        "every edit — that's the load-bearing 'diff-first' rule"
    )


def test_turn_budget_surface_in_prompt() -> None:
    """The prompt must surface the turn budget so the model can self-pace.
    The runtime injects the actual number ('You have at most {N} turns').
    The static prompt should reference 'turn budget' as a concept the
    model is told about."""
    text = _read_correction_prompt().lower()
    assert "turn budget" in text, (
        "correction.md must mention 'turn budget' so the prompt explains "
        "why the agent should self-pace rather than spiral"
    )


def test_correction_exhausted_named_in_prompt() -> None:
    """The new terminal status must be referenced in the prompt so the
    agent understands what happens if it spirals — that's part of why
    it self-paces."""
    text = _read_correction_prompt()
    assert "correction_exhausted" in text, (
        "correction.md must name the `correction_exhausted` status so "
        "the agent knows what its budget violation triggers"
    )


def test_no_plug_rule_extends_to_sofp_sub_catchalls() -> None:
    """RUN-REVIEW §3.3-E: the correction prompt's no-plug rule used to
    list only SOPL catch-alls (Administrative expenses, Other expenses).
    P1-2 extends it to SOFP-Sub catch-alls so the corrector can't 'fix'
    a SOFP imbalance by stuffing 'Other property, plant and equipment'."""
    text = _read_correction_prompt().lower()
    assert "other property, plant and equipment" in text, (
        "correction.md must enumerate SOFP-Sub catch-alls in the no-plug "
        "rule — the SOPL-only list left a gap that RUN-REVIEW §3.3-E hit"
    )
