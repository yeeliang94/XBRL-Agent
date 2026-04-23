"""Phase 6 prompt-reinforcement pin tests.

Each test pins a specific phrase in a prompt file so a well-meaning future
edit can't silently drop the reinforcement.
"""
from __future__ import annotations

import re
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flatten(s: str) -> str:
    """Collapse whitespace so phrase assertions survive line breaks in source."""
    return re.sub(r"\s+", " ", s).lower()


def test_notes_base_prompt_contains_non_duplication_rule():
    """Step 6.1: base prompt must state the 'one note, one cell' invariant."""
    text = (_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8")
    flat = _flatten(text)
    # The headline invariant — matched loosely so wording can evolve
    # without forcing a test edit every time.
    assert "exactly one cell" in flat or "one cell" in flat
    assert "appears" in flat or "appear" in flat
    # Sub-note grouping guidance should also be present.
    assert "5.1" in text  # example sub-note number
    # Explicit "same sub-note cannot appear in two cells" phrasing.
    assert "two cells" in flat or "both sheets" in flat


def test_accounting_policies_prompt_has_heading_rule():
    """Step 6.2: Sheet-11 prompt pins the material-policies heading rule."""
    text = (_PROMPT_DIR / "notes_accounting_policies.md").read_text(encoding="utf-8")
    flat = _flatten(text)
    # Must call out each of the three canonical heading variants.
    assert "material accounting policies" in flat
    assert "significant accounting policies" in flat
    assert "summary of material accounting policies" in flat
    # Must also explicitly say content doesn't belong on both sheets.
    assert "no content belongs on both" in flat or "belongs on both" in flat


def test_listofnotes_prompt_has_heading_rule():
    """Step 6.3: Sheet-12 prompt mirrors the heading-rule invariant."""
    text = (_PROMPT_DIR / "notes_listofnotes.md").read_text(encoding="utf-8")
    flat = _flatten(text)
    # Sheet-12 agents must be told to skip notes whose heading matches
    # the material-policies pattern.
    assert "material accounting policies" in flat
    assert "significant accounting policies" in flat
    assert "skip" in flat
