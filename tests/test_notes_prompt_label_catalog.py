"""Phase 3 MPERS hardening — the system prompt must seed the full
template label catalog so agents pick labels from the live MPERS/MFRS
vocabulary rather than their training-prior taxonomy memory.

Red tests for `docs/PLAN-mpers-notes-hardening.md` Phase 3.

The bug from run #105: GPT-5.4 defaulted to MFRS-flavoured labels like
`"Disclosure of fair value measurement"` which don't exist on MPERS.
Seeding the real template labels up-front eliminates that class of
dead write.
"""
from __future__ import annotations

import re

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType


def _flatten(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


# ---------------------------------------------------------------------------
# 3.1 prompt renders a label catalog when one is passed
# ---------------------------------------------------------------------------

def test_prompt_contains_label_catalog_when_provided():
    """When `label_catalog` is passed, the rendered prompt must contain
    a dedicated block listing the labels. Exact block header is part
    of the contract so tests can locate it."""
    catalog = [
        "Disclosure of capital and reserves [text block]",
        "Disclosure of cash and cash equivalents [text block]",
        "Disclosure of credit risk [text block]",
    ]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
        label_catalog=catalog,
    )
    # A distinctive, grep-able section header so downstream consumers
    # (tests, ops-level debuggers) can isolate the block.
    assert "TEMPLATE ROW LABELS" in prompt, (
        "Label catalog block header missing — agents won't know the "
        "seeded labels exist."
    )
    # Each canary label appears verbatim.
    for label in catalog:
        assert label in prompt, f"Label {label!r} missing from catalog block"


def test_prompt_omits_catalog_block_when_none():
    """Backwards-compat: omitting `label_catalog` must keep the
    pre-Phase-3 prompt shape so callers that haven't migrated don't
    regress."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
    )
    assert "TEMPLATE ROW LABELS" not in prompt


def test_prompt_label_catalog_respects_filing_standard():
    """MFRS seeded catalog shows bare labels; MPERS seeded catalog
    shows the `[text block]` suffix. Each path must pass its standard-
    specific catalog verbatim into the prompt."""
    mfrs_catalog = ["Disclosure of cash and cash equivalents"]
    mpers_catalog = ["Disclosure of cash and cash equivalents [text block]"]
    mfrs_prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=mfrs_catalog,
    )
    mpers_prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mpers",
        label_catalog=mpers_catalog,
    )
    # The rendered catalog bullets must reflect each standard's raw
    # label form. We scan bullet lines (lines that start with `  -`)
    # so prose mentions of `[text block]` in the explanation paragraph
    # don't false-positive the check.
    mfrs_bullets = [
        ln for ln in mfrs_prompt.splitlines() if ln.lstrip().startswith("- ")
    ]
    mpers_bullets = [
        ln for ln in mpers_prompt.splitlines() if ln.lstrip().startswith("- ")
    ]
    assert any("Disclosure of cash and cash equivalents" in ln
               and "[text block]" not in ln
               for ln in mfrs_bullets), (
        "MFRS catalog bullet missing the bare-form canary label."
    )
    assert any("Disclosure of cash and cash equivalents [text block]" in ln
               for ln in mpers_bullets), (
        "MPERS catalog bullet missing the [text block] canary label."
    )


def test_prompt_label_catalog_truncates_large_sheets():
    """A 138-row MFRS List-of-Notes catalog would balloon the prompt
    if rendered in full. The block must cap at a bounded row count
    and emit a visible footer pointing at `read_template` as the
    fallback retrieval path."""
    big_catalog = [f"Disclosure of concept {i}" for i in range(300)]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=big_catalog,
    )
    # Truncation footer mentioning read_template.
    flat = _flatten(prompt)
    assert "read_template" in flat, (
        "Large catalog rendered without read_template fallback hint — "
        "agent has no way to retrieve rows past the truncation."
    )
    # The actual cap is a soft contract. We assert the prompt doesn't
    # carry MORE than, say, 200 concept rows — generous ceiling so the
    # implementation can tune the exact cap without breaking the test.
    concept_lines = [
        line for line in prompt.splitlines() if "Disclosure of concept" in line
    ]
    assert len(concept_lines) <= 200, (
        f"Catalog block appears unbounded ({len(concept_lines)} rows)"
    )


# ---------------------------------------------------------------------------
# 3.2 create_notes_agent must load the catalog from the template path
# so the system prompt is seeded automatically (no caller changes).
# ---------------------------------------------------------------------------

def test_create_notes_agent_seeds_mpers_label_catalog(tmp_path):
    """Full wiring test: `create_notes_agent` must read the MPERS
    template's col-A labels, store them on `deps.template_label_catalog`,
    and surface them in the system prompt.

    We use `model="test"` (the same stub other factory tests use) so no
    provider credentials are required. Assertions lean on `deps` rather
    than poking at pydantic-ai's private system_prompt field — `deps`
    is the stable public contract.
    """
    from notes.agent import create_notes_agent
    from notes_types import NotesTemplateType

    _, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path="data/nonexistent.pdf",  # never read at factory time
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        filing_standard="mpers",
    )
    # The MPERS List-of-Notes template has 83+ disclosure rows with the
    # `[text block]` suffix. Sanity-check a handful landed on deps.
    assert deps.template_label_catalog, (
        "Factory produced an empty label catalog — the template load "
        "failed silently."
    )
    assert "Disclosure of cash and cash equivalents [text block]" in (
        deps.template_label_catalog
    ), "MPERS canary label missing from the seeded catalog on deps."
    assert "Disclosure of credit risk [text block]" in (
        deps.template_label_catalog
    )


def test_create_notes_agent_mfrs_catalog_has_no_text_block_suffix(tmp_path):
    """Regression guard: MFRS runs must seed bare-form labels (no
    `[text block]`). Confirms the factory reads from the right template
    dir — not just that it reads *something*."""
    from notes.agent import create_notes_agent
    from notes_types import NotesTemplateType

    _, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path="data/nonexistent.pdf",
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        filing_standard="mfrs",
    )
    assert deps.template_label_catalog
    # MFRS labels are bare — no row should carry the MPERS suffix.
    with_suffix = [lbl for lbl in deps.template_label_catalog
                   if "[text block]" in lbl.lower()]
    assert not with_suffix, (
        f"MFRS catalog carrying MPERS-style suffixes: {with_suffix[:3]}"
    )
