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
