"""SOPL coarse-fill posture (2026-06-09).

Colleague feedback: unlike the other statements, SOPL should follow the face
statement's figures as-is and NOT chase note-level breakdowns. Real Malaysian
filings disclose the income statement coarsely ("Others" lumped), so an agent
told to decompose comprehensively loops and over-buckets. The fix is at the
prompt layer (`prompts/sopl.md`): take the face figure as truth, and for the
handful of face lines that are formulas pulling from the Analysis sub-sheet,
write the single face figure into that section's catch-all leaf — never split.

These tests pin the prompt's behavioural contract so a casual reword can't
silently re-open the decompose-and-loop behaviour. The companion guard in
``tests/test_prompt_residual_plug_rule.py`` pins that the no-plug rule SURVIVES
this rewrite — the two must stay mutually consistent (see CLAUDE.md gotcha #17).
"""
from __future__ import annotations

import re
from pathlib import Path

_SOPL = Path(__file__).resolve().parent.parent / "prompts" / "sopl.md"


def _text() -> str:
    return _SOPL.read_text(encoding="utf-8")


def test_sopl_prompt_instructs_coarse_no_decomposition():
    """The new posture must be explicit: take the face figure as-is and do NOT
    split SOPL revenue/expenses into the granular sub-sheet fields."""
    lower = _text().lower()
    # The "do not split / stay coarse" instruction must be present.
    assert "do not split" in lower or "not split" in lower, (
        "sopl.md must tell the agent NOT to split the breakdown"
    )
    assert "coarse" in lower, (
        "sopl.md must frame the SOPL posture as coarse recording"
    )
    # The face statement is the source of truth.
    assert "face" in lower and "as-is" in lower, (
        "sopl.md must say the face figure is taken as-is"
    )


def test_sopl_prompt_overrides_base_extraction_procedure():
    """The shared `_base.md` ACCOUNTANT EXTRACTION PROCEDURE mandates
    following note references and filling component rows before lumping — the
    opposite of SOPL's coarse policy. Both prompts reach the agent in one
    system prompt, so sopl.md must EXPLICITLY mark itself the exception, or the
    base procedure silently countermands the coarse posture (code-review
    finding, 2026-06-09). Pin the explicit override so a reword can't drop it."""
    # Collapse whitespace so a line-wrapped "ACCOUNTANT EXTRACTION\nPROCEDURE"
    # still matches the phrase.
    lower = re.sub(r"\s+", " ", _text().lower())
    assert "accountant extraction procedure" in lower and "exception" in lower, (
        "sopl.md must explicitly state it is the exception to the base "
        "ACCOUNTANT EXTRACTION PROCEDURE — otherwise the system prompt's "
        "note-following rule countermands the coarse posture"
    )


def test_sopl_prompt_routes_rollup_lines_to_catchall_leaf():
    """The formula-driven face lines (Revenue, Cost of sales, Other income,
    Employee benefits, Other expenses, Finance income) can't be written
    directly — the single face figure must land in the section's catch-all
    leaf so the face formula resolves. The prompt must say so."""
    lower = _text().lower()
    assert "catch-all leaf" in lower or "catch-all" in lower, (
        "sopl.md must direct the rollup figure to the section's catch-all leaf"
    )
    # Must not hard-code a row number — labels differ across MFRS/MPERS &
    # Function/Nature, so the agent must read it off the template.
    assert "read_template" in _text(), (
        "sopl.md must tell the agent to find the catch-all leaf via "
        "read_template() rather than assuming a fixed row/label"
    )


def test_sopl_prompt_grounds_coarse_write_with_page_citation():
    """A page-cited coarse write is what keeps this on the right side of the
    reviewer's no-plug guard (a PDF-cited 'Other …' write is allowed; an
    arithmetic-only one is refused). The prompt must require grounding."""
    lower = _text().lower()
    assert "evidence" in lower or "cited" in lower or "source" in lower, (
        "sopl.md must require the coarse write to cite the face page as "
        "evidence/source (keeps it distinct from a plug)"
    )


def test_sopl_prompt_no_longer_demands_note_diving():
    """Negative assertions: the decompose-and-loop wording that drove the
    original failure must be gone. If any of these reappear, the agent is
    being pushed back toward chasing note breakdowns."""
    lower = _text().lower()
    forbidden = [
        "fill the analysis sub-sheet first",
        "view the note pages to read the breakdown",
        "find the missing component in the notes",
    ]
    for phrase in forbidden:
        assert phrase not in lower, (
            f"sopl.md still contains decompose-era wording: {phrase!r}"
        )


def test_sopl_prompt_keeps_no_plug_rule():
    """The coarse posture must NOT weaken the no-plug rule. This mirrors
    ``test_prompt_residual_plug_rule::test_sopl_prompt_constrains_catchall_language``
    so the two stay consistent: recording a real coarse figure is allowed;
    inventing a residual to force a balance is not (gotcha #17)."""
    lower = _text().lower()
    assert "never" in lower and (
        "balancing" in lower or "plug" in lower or "residual" in lower
    ), (
        "sopl.md must keep the no-balancing-plug rule alongside the coarse "
        "posture — coarse recording is fine, plugging is not"
    )
