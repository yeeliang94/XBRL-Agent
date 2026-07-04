"""Top-line routing + accounting-policy carve-out — prompt-contract pins.

Phase 1 of docs/PLAN-notes-coverage-and-routing.md (PRD:
docs/PRD-notes-coverage-and-routing.md). Two confirmed business rules
(2026-07-04):

1. Content follows its TOP-LINE note, whole — a sub-section is never moved
   to another row because of its title or the topics it mentions.
2. The ONLY cross-sheet carve-out trigger is the explicit
   "material/significant accounting policy" label, in both directions:
   a labelled sub-section embedded in a topical note goes to the
   Accounting Policies sheet; anything else (including "Policy on X"
   without the label) stays with its top-line note.

These tests pin the rule wording in the three prompt files plus the
rendered prompt, so a later prompt edit can't silently soften either rule
(the same pattern as tests/test_notes_prompt_phase1.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flatten(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def _base() -> str:
    return _flatten((_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8"))


def test_base_prompt_has_carve_out_section():
    """The base prompt must carry the carve-out rule with the explicit-label
    trigger naming both accepted phrasings."""
    flat = _base()
    assert "accounting-policy carve-out" in flat
    assert "material accounting policy" in flat
    assert "significant accounting policy" in flat
    # The trigger must be framed as explicit-label-only.
    assert "explicit label" in flat


def test_base_prompt_names_the_non_triggers():
    """'Policy on <topic>' without the label, and mere topic mentions, must
    be explicitly called out as NOT triggering a carve-out."""
    flat = _base()
    assert "does not trigger a carve-out" in flat
    assert "policy on <topic>" in flat
    assert "without the material/significant label" in flat
    # The leases-inside-PP&E mention rule.
    assert "merely mentioned" in flat
    assert "never the leases row" in flat


def test_base_prompt_has_the_worked_example():
    """The confirmed investment-properties worked example must be present
    with all four dispositions (stays / stays / carved out / stays)."""
    flat = _base()
    assert "investment properties" in flat
    assert "fair-value movement table" in flat
    assert "carved out" in flat
    assert "rental income from operating leases" in flat


def test_base_prompt_frames_carve_out_as_partition_not_duplication():
    """The carve-out must be reconciled with the NO CROSS-SHEET DUPLICATION
    invariant: it partitions different content, it never duplicates."""
    flat = _base()
    assert "partition" in flat
    assert "not duplication" in flat or "not a violation" in flat
    # The carved section must not ALSO remain in the disclosure cell.
    assert "must not also remain in the disclosure cell" in flat


def test_accounting_policies_prompt_hunts_carved_out_sections():
    """The policies-sheet agent owns collection of embedded labelled policy
    sub-sections — via a search_pdf_text sweep after the main section."""
    flat = _flatten(
        (_PROMPT_DIR / "notes_accounting_policies.md").read_text(encoding="utf-8")
    )
    assert "carved-out policy sub-sections" in flat
    assert "search_pdf_text" in flat
    assert "material accounting policy" in flat
    # Explicit-label-only scoping: unlabelled policy prose is not harvested.
    assert "explicit label only" in flat
    assert "do not harvest" in flat


def test_listofnotes_prompt_excludes_only_labelled_sections():
    """The Sheet-12 agent excludes labelled policy sub-sections from its
    cells but keeps everything else whole under the top-line note."""
    flat = _flatten(
        (_PROMPT_DIR / "notes_listofnotes.md").read_text(encoding="utf-8")
    )
    assert "carve out only the labelled ones" in flat
    assert "exclude it from your payload" in flat
    # The note still counts as written in the coverage receipt.
    assert 'counts as "written"' in flat
    # Non-triggers stay.
    assert "without the label stays" in flat
    assert "merely mentioned in the note stays" in flat


def test_rendered_listofnotes_prompt_carries_the_carve_out_rule():
    """The rule must survive prompt rendering (base + sheet-specific are
    composed) on both filing standards."""
    for standard in ("mfrs", "mpers"):
        prompt = render_notes_prompt(
            NotesTemplateType.LIST_OF_NOTES,
            filing_level="company",
            inventory=[],
            filing_standard=standard,
        )
        flat = _flatten(prompt)
        assert "accounting-policy carve-out" in flat, standard
        assert "exclude it from your payload" in flat, standard


def test_rendered_accounting_policies_prompt_carries_the_sweep():
    for standard in ("mfrs", "mpers"):
        prompt = render_notes_prompt(
            NotesTemplateType.ACC_POLICIES,
            filing_level="company",
            inventory=[],
            filing_standard=standard,
        )
        flat = _flatten(prompt)
        assert "accounting-policy carve-out" in flat, standard
        assert "search_pdf_text" in flat, standard
