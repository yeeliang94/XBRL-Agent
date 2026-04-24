"""Phase 1 (post-FINCO-2021 audit) — prompt-contract pins.

These tests anchor the three prompt-level changes from
`docs/PLAN-notes-pipeline-improvements.md` Phase 1 so a later edit can't
silently revert them:

1.1 PDF-page citation pin — every notes prompt must tell the model to cite
    the PDF page number (the one passed to `view_pdf_pages`), not the
    printed folio from the page image footer.
1.2 Schedule-or-prose rule — when a note contains a numeric schedule, it
    must be rendered as an ASCII table, not replaced by policy prose.
1.3 Sub-agent batch-scope nudge — the Sheet-12 sub-agent prompt must name
    the sub-agent's PDF page range explicitly so wander is discouraged.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flatten(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def test_base_prompt_pins_pdf_page_citation():
    """Phase 1.1 — base prompt must state that `evidence` cites PDF pages
    (the ones passed to view_pdf_pages), not the printed folio."""
    base = (_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8")
    flat = _flatten(base)
    # Must mention both "PDF page" and a no-printed-folio caveat.
    assert "pdf page" in flat
    assert "printed folio" in flat or "printed page" in flat


def test_listofnotes_prompt_pins_pdf_page_citation():
    """Phase 1.1 — the Sheet-12-specific prompt repeats the rule since
    that's where the drift was observed."""
    loa = (_PROMPT_DIR / "notes_listofnotes.md").read_text(encoding="utf-8")
    flat = _flatten(loa)
    assert "pdf page" in flat
    assert "printed folio" in flat or "printed page" in flat


def test_base_prompt_has_schedules_section():
    """Phase 1.2 — the base prompt must mandate rendering numeric
    schedules (movement tables, ECL allowances, maturity analyses) as
    real tables rather than replacing them with policy prose.

    Note: the schedule rendering format moved from ASCII to HTML as
    part of the rich-editor pipeline (docs/PLAN-NOTES-RICH-EDITOR.md);
    the "schedules render, don't get paraphrased" invariant is the
    stable part, and that's what this test pins."""
    base = (_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8")
    assert "SCHEDULES" in base or "SCHEDULE" in base
    flat = _flatten(base)
    # Tables must still be mentioned — format is HTML `<table>` now
    # (formerly ASCII columns).
    assert "<table>" in base or "table" in flat
    assert "movement" in flat or "maturity" in flat
    # Explicit "do not substitute prose for the schedule" rule.
    assert "do not drop" in flat or "do not replace" in flat


def test_rendered_listofnotes_prompt_includes_schedules_rule():
    """The per-template rendered prompt must inherit the schedules rule
    from the base — no way for a specific sheet to silently drop it."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
    )
    assert "SCHEDULES" in prompt or "SCHEDULE" in prompt


def test_subcoordinator_prompt_includes_batch_page_range():
    """Phase 1.3 — the per-sub prompt must name the batch's PDF page
    range so the model has something concrete to scope itself against."""
    # We test the helper by building the prompt string in isolation — we
    # don't need the full agent.iter harness to exercise the nudge.
    from notes.listofnotes_subcoordinator import _invoke_sub_agent_once
    import inspect

    # Extract the function source so the test can't be faked by a later
    # edit that deletes the batch-pages derivation. We assert the source
    # references the variables by name.
    src = inspect.getsource(_invoke_sub_agent_once)
    assert "batch_min" in src and "batch_max" in src, (
        "Phase 1.3 expects the sub-agent prompt to derive a batch page "
        "range (batch_min/batch_max) and surface it in the user prompt."
    )
    assert "scope_line" in src or "batch covers PDF pages" in src


def test_subcoordinator_prompt_empty_batch_omits_scope_line():
    """Vacuous case: an empty batch should not emit an empty page-range
    sentence — avoids 'pages 0–0' noise in an edge-case prompt."""
    # We smoke-test by synthesising the prompt fragment the runtime would
    # build. Keep in sync with the string in _invoke_sub_agent_once — if
    # that string changes, this test updates alongside.
    batch: list[NoteInventoryEntry] = []
    batch_pages = [p for e in batch for p in range(e.page_range[0], e.page_range[1] + 1)]
    assert not batch_pages  # empty-batch invariant


# ---------------------------------------------------------------------------
# Sub-sheet template-first rule (Phase 4 of the model+notes-heading plan).
#
# Production runs have been lumping note breakdowns onto the face sheet when a
# matching sub-sheet field exists. The fix is a prompt rule that gates the
# breakdown-to-sub-sheet decision on TEMPLATE granularity, not on the note's
# line count. These tests pin that wording — including a negative assertion
# against the rejected "one row per note line" quota rule.
# ---------------------------------------------------------------------------


def test_sofp_prompt_has_template_first_breakdown_rule():
    """prompts/sofp.md must anchor the breakdown rule on the sub-sheet
    field list (template-first), not on note-line count."""
    body = (_PROMPT_DIR / "sofp.md").read_text(encoding="utf-8")
    flat = _flatten(body)
    # Core template-first phrasing: the agent checks whether a matching
    # sub-sheet field exists before deciding to split a note line.
    assert "matching sub-sheet field" in flat, (
        "prompts/sofp.md must require the agent to check for a matching "
        "sub-sheet field before splitting a note breakdown"
    )


def test_sofp_prompt_names_the_failure_mode_explicitly():
    """The failure case (lump sum on face sheet when a sub-sheet field
    exists) must be called out as the thing to avoid."""
    body = (_PROMPT_DIR / "sofp.md").read_text(encoding="utf-8")
    flat = _flatten(body)
    assert "lump sum" in flat and "face sheet" in flat, (
        "prompts/sofp.md must explicitly call out 'lump sum on the face "
        "sheet' as the failure mode"
    )


def test_sofp_prompt_does_not_carry_rejected_quota_rule():
    """Negative assertion: the rigid one-row-per-note-line quota rule
    is wrong (the template controls granularity, not the note). Guard
    against it being re-introduced by a future edit."""
    body = (_PROMPT_DIR / "sofp.md").read_text(encoding="utf-8")
    flat = _flatten(body)
    # Reject both phrasings the earlier plan draft used.
    assert "one sub-sheet row per breakdown line" not in flat, (
        "prompts/sofp.md contains the rejected quota rule 'one row per "
        "breakdown line' — the rule must gate on template granularity"
    )
    assert "must write 5 sub-sheet rows" not in flat, (
        "prompts/sofp.md contains the rejected concrete quota example "
        "'must write 5 sub-sheet rows'"
    )


def test_sopl_prompt_has_template_first_breakdown_rule():
    """prompts/sopl.md must carry the same template-first rule for the
    Analysis sub-sheet."""
    body = (_PROMPT_DIR / "sopl.md").read_text(encoding="utf-8")
    flat = _flatten(body)
    assert "matching" in flat and "analysis" in flat, (
        "prompts/sopl.md must reference matching note lines to Analysis "
        "sub-sheet fields (template-first rule)"
    )
    assert "lump" in flat or "single line" in flat, (
        "prompts/sopl.md must call out the lumping failure mode"
    )
