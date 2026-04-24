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
    assert "=== TEMPLATE ROW LABELS (copy verbatim) ===" in prompt, (
        "Label catalog block header missing — agents won't know the "
        "seeded labels exist."
    )
    # Each canary label appears verbatim.
    for label in catalog:
        assert label in prompt, f"Label {label!r} missing from catalog block"


def test_listofnotes_tokens_do_not_leak_into_non_lon_prompts():
    """Test-gap from peer review: `_apply_listofnotes_tokens` runs on all
    template_types (no-op contract on non-LoN since placeholders don't
    appear in their prompt files). A non-LoN render must be crash-free
    AND must NOT contain literal `{{TEMPLATE_ROW_COUNT}}` / `{{CATCH_ALL_LABEL}}`
    strings, even though the substitution pipeline ran against them."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=[],  # no catalog — stress the fallback path too
    )
    assert "{{TEMPLATE_ROW_COUNT}}" not in prompt
    assert "{{CATCH_ALL_LABEL}}" not in prompt


def test_prompt_omits_catalog_block_when_none():
    """Backwards-compat: omitting `label_catalog` must keep the
    pre-Phase-3 prompt shape so callers that haven't migrated don't
    regress. We check for the BLOCK header (=== TEMPLATE ROW LABELS
    (copy verbatim) ===), not the plain substring — the LoN prompt now
    legitimately references the block's title in its fallback
    guidance (peer-review F2), which is not the same as emitting the
    block itself."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
    )
    assert "=== TEMPLATE ROW LABELS (copy verbatim) ===" not in prompt


# ---------------------------------------------------------------------------
# Peer-review F2 — when the catalog block is absent the LoN prompt text
# must still be accurate (tell the agent to call read_template as fallback,
# not claim that a non-existent seeded block lists every col-A label).
# ---------------------------------------------------------------------------

def test_list_of_notes_prompt_without_catalog_does_not_lie_about_seeded_block():
    """The prompt text historically said 'the seeded row-label catalog
    block below already lists every col-A label' unconditionally. When
    catalog loading fails (empty label_catalog), no such block is
    emitted — so that line misleads the agent. Fix is to have the
    prompt text branch on the catalog's presence, or to always include
    an explicit fallback sentence that tells the agent what to do when
    no block is present."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=[],
    )
    # Sanity: no catalog block was emitted (check the actual block header,
    # not a plain substring — the guidance text may legitimately reference
    # the block's title).
    assert "=== TEMPLATE ROW LABELS (copy verbatim) ===" not in prompt

    flat = _flatten(prompt)

    # The "already lists every col-A label" phrase is the specific lie —
    # it asserts the seeded block exists. Either that phrase must be
    # gone, or it must be guarded with a conditional like "if present".
    lies_about_catalog = "already lists every col-a label" in flat
    if lies_about_catalog:
        # If the phrase survives, it must be guarded by a conditional
        # clause — "if present", "when available", etc.
        guarded = any(
            qualifier in flat for qualifier in [
                "if present", "when present", "if available", "when available",
                "if a row-label catalog", "if the catalog", "when the catalog",
            ]
        )
        assert guarded, (
            "LoN prompt asserts the catalog block 'already lists every "
            "col-A label' but no catalog block was emitted. This misleads "
            "the agent — either drop the unconditional claim or guard it "
            "with an 'if present' qualifier."
        )

    # Whichever approach the fix takes, the agent MUST have actionable
    # guidance for the catalog-absent path. A fallback marker phrase ties
    # this contract to the real fix — either mention "call read_template
    # first" as an explicit fallback step, or flag "no catalog" / "catalog
    # unavailable" so the agent knows it has to go get the labels itself.
    actionable = (
        "call read_template first" in flat
        or "call `read_template` first" in flat
        or "no catalog" in flat
        or "catalog unavailable" in flat
    )
    assert actionable, (
        "LoN prompt must supply explicit fallback guidance when the "
        "seeded catalog block is absent — tell the agent to call "
        "read_template first."
    )


def test_list_of_notes_prompt_with_catalog_still_uses_catalog_instructions():
    """Regression guard for the happy path — when the catalog IS seeded,
    the prompt still tells the agent to read from the seeded block."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=[
            "Disclosure of cash and cash equivalents",
            "Disclosure of trade and other payables",
        ],
    )
    # Block must actually be emitted, not just referenced in guidance text.
    assert "=== TEMPLATE ROW LABELS (copy verbatim) ===" in prompt
    # Positive marker the agent should look at: the 'catalog' nomenclature
    # plus a reference to the seeded block must persist.
    flat = _flatten(prompt)
    assert "catalog" in flat


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
    # Post-2026-04-23 (CLAUDE.md gotcha #15): the MPERS generator now
    # strips SSM ReportingLabel suffixes (`[text block]`, `[abstract]`,
    # …) from the rendered templates so Column A carries the bare
    # "Disclosure of …" form — matching MFRS. The writer / coverage
    # validator still call `notes.labels.normalize_label` defensively
    # so agents that emit suffixed labels keep matching, but the
    # catalog seeded from the live template is bare.
    assert deps.template_label_catalog, (
        "Factory produced an empty label catalog — the template load "
        "failed silently."
    )
    assert "Disclosure of cash and cash equivalents" in (
        deps.template_label_catalog
    ), "MPERS canary label missing from the seeded catalog on deps."
    assert "Disclosure of credit risk" in (
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
