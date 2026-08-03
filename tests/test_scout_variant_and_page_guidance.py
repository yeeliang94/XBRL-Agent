"""Scout guidance must not override its own detectors or fence itself in.

Peer review, 2026-08-01. Two lines in the scout system prompt worked against
the rest of the system:

1. `- **SOCIE**: always "Default".` — flatly wrong on MPERS, where an entity
   may present only a Statement of Retained Earnings (SoRE). The deterministic
   `check_variant_signals` tool was deliberately made standard-aware so it
   could score SoRE; the prompt told the model to ignore that result. Routing
   a SoRE filing to the Default template sends it to a different template with
   different concept uuids (gotcha #21).

2. `- Only look at pages near the TOC-stated page (±10 pages).` — a hard
   restriction. Nothing enforces it (`view_pages` validates only 1 <= page <=
   N), so its only effect is to make the model give up on a statement sitting
   outside the window, which the prompt then instructs it to omit.
"""
from __future__ import annotations

from scout.agent import _SYSTEM_PROMPT

# The template carries one placeholder; these assertions are about the static
# guidance around it, so a bare fill is enough.
SCOUT_SYSTEM_PROMPT = _SYSTEM_PROMPT.format(statements_section="")


def test_scout_does_not_claim_socie_is_always_default():
    assert 'always "Default"' not in SCOUT_SYSTEM_PROMPT
    assert "always Default" not in SCOUT_SYSTEM_PROMPT


def test_scout_knows_sore_exists_and_defers_to_the_detector():
    """The variant rule must name SoRE, scope it to MPERS, and point at the
    deterministic tool rather than asking for a bare visual judgement."""
    text = SCOUT_SYSTEM_PROMPT
    assert "SoRE" in text
    assert "check_variant_signals" in text
    # SoRE is MPERS-only — the scout must not offer it on an MFRS filing.
    assert "does not exist on MFRS" in text


def test_scout_page_window_is_a_starting_point_not_a_boundary():
    text = SCOUT_SYSTEM_PROMPT
    assert "Only look at pages near" not in text
    # The soft-hint contract (gotcha #13) must be stated positively, so the
    # model knows widening the search is allowed rather than merely untested.
    assert "any page" in text.lower()
    assert "±10" in text or "+/-10" in text


def test_scout_still_prefers_the_toc_window_first():
    """Softening the rule must not turn the scout into a full-document
    scanner — the cost control ('view only the pages you need') stays."""
    assert "view only the pages you need" in SCOUT_SYSTEM_PROMPT
