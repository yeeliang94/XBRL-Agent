"""Phase 1 MPERS hardening — `render_notes_prompt` must branch on
`filing_standard` and emit the correct sheet numbering per standard.

Red tests for `docs/PLAN-mpers-notes-hardening.md` Phase 1. The failing
run (#105, 2026-04-23) skipped MPERS notes citing MFRS sheet numbers
because the prompt was hardcoded to MFRS 10-14 regardless of standard.
"""
from __future__ import annotations

import re

import pytest

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType


def _flatten(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


# ---------------------------------------------------------------------------
# 1.1: render_notes_prompt must accept filing_standard without TypeError
# ---------------------------------------------------------------------------

def test_render_notes_prompt_accepts_filing_standard():
    """The signature must accept `filing_standard` — the kwarg is the
    channel every later phase branches on."""
    # No assertion on content here; the call-site existence is what we
    # need so `create_notes_agent` can pass `filing_standard` through.
    render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )


def test_render_notes_prompt_rejects_unknown_standard():
    """Unknown standards must fail fast rather than silently fall back
    to MFRS — the run-level dispatcher already validates the axis, so
    if something invalid reaches here it's a wiring bug."""
    with pytest.raises(ValueError):
        render_notes_prompt(
            template_type=NotesTemplateType.LIST_OF_NOTES,
            filing_level="company",
            inventory=[],
            filing_standard="ifrs",
        )


# ---------------------------------------------------------------------------
# 1.2: MPERS renders MPERS sheet numbering (11-15) and calls out SoRE at 10
# ---------------------------------------------------------------------------

def test_render_notes_prompt_mpers_shows_mpers_sheet_map():
    """MPERS slots notes at 11-15; slot 10 is the MPERS-only SoRE
    face-statement template (CLAUDE.md gotcha #15). The sheet map in
    the prompt must reflect that layout so the agent's cross-sheet
    skip reasoning cites the right sheet numbers."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )
    flat = _flatten(prompt)
    # Each notes template under MPERS numbering.
    assert "sheet 11" in flat and "corporate information" in flat
    assert "sheet 12" in flat and "accounting polic" in flat
    assert "sheet 13" in flat and "list of notes" in flat
    assert "sheet 14" in flat and "issued capital" in flat
    assert "sheet 15" in flat and "related party" in flat
    # MPERS-only SoRE at slot 10.
    assert "sheet 10" in flat
    assert "retained earnings" in flat or "sore" in flat


def test_render_notes_prompt_mfrs_preserves_existing_sheet_map():
    """Regression guard: MFRS runs must continue to see the
    pre-existing 10-14 sheet map. No MPERS-only references should
    leak into an MFRS prompt."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
    )
    flat = _flatten(prompt)
    assert "sheet 10" in flat and "corporate information" in flat
    assert "sheet 11" in flat and "accounting polic" in flat
    assert "sheet 12" in flat and "list of notes" in flat
    assert "sheet 13" in flat and "issued capital" in flat
    assert "sheet 14" in flat and "related party" in flat
    # MPERS-only SoRE must NOT appear on an MFRS prompt.
    assert "retained earnings" not in flat or "statement of retained earnings" not in flat


def test_render_notes_prompt_default_is_mfrs():
    """Backwards-compat: omitting the kwarg must keep the historical
    MFRS behaviour so callers that haven't migrated yet don't regress."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
    )
    flat = _flatten(prompt)
    assert "sheet 10" in flat and "corporate information" in flat


# ---------------------------------------------------------------------------
# 1.3: per-template prompts must not ship raw MFRS sheet numbers that
# leak into an MPERS rendering
# ---------------------------------------------------------------------------

def test_mpers_rendered_prompt_has_no_mfrs_sheet_map_leak():
    """The hardcoded `Sheet 10 - Corporate Information` table in
    `_notes_base.md` / `notes_listofnotes.md` must NOT survive into an
    MPERS rendering. Presence = regression back to the run-#105 bug."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )
    flat = _flatten(prompt)
    # On MPERS, "Sheet 10 — Corporate Information" is the MFRS mapping.
    # The MPERS map puts Corporate Information on Sheet 11. If the old
    # hardcoded string leaks, the adjacency below will be present.
    assert "sheet 10 — corporate information" not in flat
    assert "sheet 10 - corporate information" not in flat
    # Same for accounting policies (Sheet 11 on MFRS, Sheet 12 on MPERS).
    assert "sheet 11 — summary of material accounting polic" not in flat
    assert "sheet 11 - summary of material accounting polic" not in flat
    # And for List of Notes (Sheet 12 on MFRS, Sheet 13 on MPERS).
    assert "sheet 12 — list of notes" not in flat
    assert "sheet 12 - list of notes" not in flat


# ---------------------------------------------------------------------------
# 4: MPERS-specific overlay — the prompt must tell the agent about the
# [text block] suffix, the smaller concept set, and the SoRE slot.
# ---------------------------------------------------------------------------

def test_mpers_prompt_mentions_text_block_suffix():
    """The MPERS overlay must surface the `[text block]` taxonomy
    convention so the agent knows MPERS labels look different from
    their training-prior MFRS memory."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )
    flat = _flatten(prompt)
    assert "[text block]" in flat, (
        "MPERS overlay missing the [text block] suffix explanation — "
        "agents will keep emitting bare MFRS-style labels."
    )


def test_mpers_prompt_mentions_smaller_concept_set():
    """The MPERS overlay must warn the agent that the MPERS taxonomy
    is narrower than MFRS so it doesn't fabricate labels (the run-#105
    'Disclosure of capital management' / 'fair value measurement'
    failure mode)."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )
    flat = _flatten(prompt)
    # Accept any of the natural phrasings — the test is about the
    # message being present, not a specific wording.
    assert ("smaller" in flat or "fewer" in flat or "narrower" in flat), (
        "MPERS overlay missing the narrow-taxonomy warning."
    )


def test_mfrs_prompt_has_no_mpers_overlay_leak():
    """Regression: the overlay must gate strictly on `filing_standard`.
    An MFRS run picking up MPERS guidance would destabilise the MFRS
    pipeline for no benefit."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
    )
    flat = _flatten(prompt)
    assert "[text block]" not in flat or "mpers" not in flat, (
        "MFRS prompt leaked an MPERS-specific overlay block."
    )


def test_render_notes_prompt_no_unresolved_cross_sheet_tokens():
    """Any `{{CROSS_SHEET:<topic>}}` token left in the rendered output
    means the Python-side substitution missed the topic key. Failing
    here flags a typo in either the prompt file or the mapping before
    a live run sees a literal `{{CROSS_SHEET:…}}` in the system prompt."""
    for standard in ("mfrs", "mpers"):
        for ttype in (NotesTemplateType.LIST_OF_NOTES,
                      NotesTemplateType.ACC_POLICIES,
                      NotesTemplateType.CORP_INFO,
                      NotesTemplateType.ISSUED_CAPITAL,
                      NotesTemplateType.RELATED_PARTY):
            prompt = render_notes_prompt(
                template_type=ttype,
                filing_level="company",
                inventory=[],
                filing_standard=standard,
            )
            assert "{{CROSS_SHEET:" not in prompt, (
                f"Unresolved CROSS_SHEET token in {ttype}/{standard} — "
                f"either the prompt file uses an unknown topic key or "
                f"the mapping is out of sync."
            )


# ---------------------------------------------------------------------------
# 5: List-of-Notes prompt must drop MFRS-specific hardcoded numbers and
# pick the row count / catch-all label from the live template catalog.
# Regression for the run-#107 (816d0389) warning storm where the MPERS
# sub-agent emitted MFRS-only labels ("Disclosure of allowance for
# credit losses", "Disclosure of capital management", …) after being
# primed by the "full template has 138 rows" line.
# ---------------------------------------------------------------------------

def test_listofnotes_prompt_row_count_reflects_catalog_size():
    """`{{TEMPLATE_ROW_COUNT}}` must resolve to the seeded catalog's
    length so an MPERS run sees "84 rows" instead of inheriting the
    MFRS "138 rows" anchor that primed the training-prior taxonomy."""
    mpers_catalog = [f"Disclosure of concept {i}" for i in range(84)]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
        label_catalog=mpers_catalog,
    )
    flat = _flatten(prompt)
    assert "{{TEMPLATE_ROW_COUNT}}" not in prompt, (
        "TEMPLATE_ROW_COUNT placeholder left unresolved — wiring gap "
        "in _apply_listofnotes_tokens."
    )
    assert "138 rows" not in flat, (
        "MFRS-hardcoded '138 rows' leaked into MPERS prompt — agents "
        "will recall the MFRS label set from training prior."
    )
    assert "84 rows" in flat, (
        "MPERS catalog size didn't make it into the prompt — the agent "
        "no longer has a concrete size anchor for the MPERS taxonomy."
    )


def test_listofnotes_prompt_catch_all_label_from_catalog():
    """`{{CATCH_ALL_LABEL}}` must resolve to the actual catch-all row
    from the seeded catalog so the "route unmatched notes here"
    instruction cites a label the writer will actually resolve."""
    mpers_catalog = [
        "Disclosure of leases",
        "Disclosure of other notes to accounts",
        "Disclosure of revenue",
    ]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
        label_catalog=mpers_catalog,
    )
    assert "{{CATCH_ALL_LABEL}}" not in prompt, (
        "CATCH_ALL_LABEL placeholder left unresolved."
    )
    assert "Disclosure of other notes to accounts" in prompt, (
        "Catch-all label reference missing — sub-agents will not know "
        "where to route unmatched notes."
    )


def test_listofnotes_prompt_catch_all_prefers_catalog_verbatim():
    """If the catalog carries a suffixed form of the catch-all row
    (e.g. an older MPERS snapshot with `[text block]`), the resolver
    must pass that suffixed form through untouched so the writer's
    exact-match path hits on the live template."""
    suffixed_catalog = [
        "Disclosure of leases [text block]",
        "Disclosure of other notes to accounts [text block]",
    ]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
        label_catalog=suffixed_catalog,
    )
    assert "Disclosure of other notes to accounts [text block]" in prompt, (
        "Catch-all resolver lost the suffix — would drop the agent on a "
        "row label that no longer exists verbatim in the template."
    )


def test_listofnotes_prompt_no_unresolved_listofnotes_tokens():
    """Regression: neither `{{TEMPLATE_ROW_COUNT}}` nor `{{CATCH_ALL_LABEL}}`
    may leak through on any combination of filing_standard and
    (catalog, no-catalog). A literal placeholder in the rendered prompt
    is a wiring bug worth flagging before a live run sees it."""
    for standard in ("mfrs", "mpers"):
        for catalog in (None, ["Disclosure of other notes to accounts"]):
            prompt = render_notes_prompt(
                template_type=NotesTemplateType.LIST_OF_NOTES,
                filing_level="company",
                inventory=[],
                filing_standard=standard,
                label_catalog=catalog,
            )
            assert "{{TEMPLATE_ROW_COUNT}}" not in prompt, (
                f"TEMPLATE_ROW_COUNT leaked on "
                f"standard={standard} catalog={'none' if catalog is None else 'live'}"
            )
            assert "{{CATCH_ALL_LABEL}}" not in prompt, (
                f"CATCH_ALL_LABEL leaked on "
                f"standard={standard} catalog={'none' if catalog is None else 'live'}"
            )


def test_listofnotes_prompt_cross_sheet_hints_match_standard():
    """`notes_listofnotes.md` used to say 'Corporate Information belongs
    on Sheet 10'. On MPERS that's Sheet 11. The rendered prompt must
    reflect the active standard when it tells the agent which sheet a
    topic belongs to."""
    mpers_prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
    )
    flat = _flatten(mpers_prompt)
    # MPERS: CORP_INFO = sheet 11 so the cross-sheet hint must cite 11.
    # Accept either phrasing — "belongs on Sheet 11" or "is on Sheet 11".
    # The original MFRS-hardcoded "Sheet 10" reference is the regression.
    if "corporate information" in flat and "belongs on sheet" in flat:
        # If the cross-sheet hint block exists at all, it must cite 11.
        assert "belongs on sheet 11" in flat, (
            "MPERS rendering still claims Corporate Information lives "
            "on an MFRS sheet number — cross-sheet hints didn't branch."
        )
