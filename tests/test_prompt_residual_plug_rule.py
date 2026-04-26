"""Bug B (2026-04-26) — prompt rules against catch-all residual plugging.

The latest extraction run wrote evidence "balancing amount to reconcile face-
statement profit before tax to RM151,570'000" into "Other miscellaneous
expenses" — the agent was using the catch-all row as a residual plug to make
verify_totals pass. The fix is at the prompt layer: tell the agent to leave
the gap honestly when the breakdown can't reconcile, not to plug.

These tests pin the prompt content. Subtle but important: prompt files are
NOT code — they're the agent's behavioural contract. Without test pinning,
a casual reword can silently re-open the plugging behaviour.
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def test_base_prompt_forbids_residual_plug():
    """The shared `_base.md` carries the global rule that applies to all
    five face-statement agents. Pin a sentinel phrase that captures the
    rule's intent — peer-review #7 (2026-04-26) flagged that loose
    keyword-presence pins can pass even after the rule is gutted, so we
    require the words to co-occur in a single sentence rather than just
    appear somewhere in the file."""
    text = (_PROMPTS / "_base.md").read_text(encoding="utf-8")
    # Sentinel phrase: the canonical statement of the rule. If a future
    # edit removes this, the test fails immediately.
    assert "NEVER use a catch-all row" in text, (
        "_base.md must contain the canonical sentinel phrase "
        "'NEVER use a catch-all row' — see CLAUDE.md gotcha #17"
    )
    # Plus the explanatory clause spelling out what NOT to do.
    lower = text.lower()
    assert "balancing figure" in lower or "balancing plug" in lower, (
        "_base.md must spell out the prohibited use case ('balancing "
        "figure' / 'balancing plug')"
    )


def test_sopl_prompt_constrains_catchall_language():
    """`prompts/sopl.md` historically read 'Administrative expenses is a
    catch-all for operating expenses when the entity doesn't break them
    down by function' with no 'do not plug' guard. The constrained version
    keeps the legitimate use case (entity disclosure is genuinely coarse)
    but pairs it with an explicit no-plug rule."""
    text = (_PROMPTS / "sopl.md").read_text(encoding="utf-8").lower()
    # Must still mention catch-all (the legitimate use case is real)
    assert "catch-all" in text or "catch all" in text
    # But must now also carry the constraint
    assert "never" in text and (
        "balancing" in text or "plug" in text or "residual" in text
    ), (
        "sopl.md must constrain the catch-all mention with a do-not-plug "
        "rule — without it the prompt invites residual stuffing"
    )


def test_sofp_prompt_forbids_sub_sheet_residual_plug():
    """RUN-REVIEW P1-2 (2026-04-26): the Amway run wrote `Other property,
    plant and equipment` 9,525 with evidence literally saying
    'Total row needed to match face PPE RM64,579'. Until P1-2 the SOFP
    prompt had no analogue of SOPL's no-plug rule, so the agent had no
    written prohibition against using sub-sheet 'Other …' rows as a
    balancing mechanism. This test pins the new no-residual-plug block
    in `prompts/sofp.md` so a future re-edit can't silently regress."""
    text = (_PROMPTS / "sofp.md").read_text(encoding="utf-8")
    assert "NO-RESIDUAL-PLUG RULE" in text, (
        "sofp.md must carry the named no-residual-plug section — the "
        "header is the test's anchor for any future re-edit"
    )
    lower = text.lower()
    # The catch-all category list must mention the PPE sub-block by name
    assert "other property, plant and equipment" in lower
    # Explicit prohibition + the SOPL-pattern wording about coarse disclosure
    assert "never plug a residual" in lower or "never plug" in lower
    assert "genuinely coarse" in lower, (
        "sofp.md should mirror SOPL's wording: catch-all rows are for "
        "entities whose disclosure is genuinely coarse, not for plugging"
    )
    # Reinforce the legitimate-mapping rule for individual PPE components
    assert "motor vehicles" in lower
    assert "construction in progress" in lower


def test_correction_prompt_forbids_residual_plug():
    """The correction agent runs after merge if cross-checks fail. It
    must NOT respond to a failed check by plugging a catch-all to make
    the next run_cross_checks pass. Pinned by sentinel phrase per
    peer-review #7 (2026-04-26)."""
    text = (_PROMPTS / "correction.md").read_text(encoding="utf-8")
    assert "NEVER write a residual" in text or "NEVER plug" in text, (
        "correction.md must contain a strong sentinel forbidding plugs — "
        "either 'NEVER write a residual' or 'NEVER plug'"
    )
    lower = text.lower()
    assert "catch-all" in lower, (
        "correction.md must name 'catch-all' explicitly so the agent "
        "knows which kind of row this rule refers to"
    )
