"""Phase 2 MPERS hardening — the fuzzy matcher must strip taxonomy
type suffixes (`[text block]`, `[abstract]`, `[axis]`, `[member]`,
`[table]`) before comparing labels.

Red tests for `docs/PLAN-mpers-notes-hardening.md` Phase 2.

The bug from run #105: MFRS template labels are bare ("Disclosure of
other income"), MPERS template labels carry the SSM ReportingLabel
type suffix ("Disclosure of other income [text block]"). Short labels
fall below the 0.85 fuzzy threshold purely because of the suffix and
get silently rejected by `_resolve_row`. We fix it by normalising the
suffix out on both sides of the comparison so the semantic match is
taxonomy-style-independent.
"""
from __future__ import annotations

from notes.writer import (
    _LabelEntry,
    _normalize,
    _resolve_row,
)


def _entries(labels: list[tuple[int, str]]) -> list[_LabelEntry]:
    return [
        _LabelEntry(normalized=_normalize(orig), row=row, original=orig)
        for row, orig in labels
    ]


# ---------------------------------------------------------------------------
# 2.1 writer — taxonomy suffix stripping in _normalize / _resolve_row
# ---------------------------------------------------------------------------

def test_resolve_row_matches_bare_label_to_text_block_label():
    """The sub-agent emits `"Disclosure of other income"`; the MPERS
    template row reads `"Disclosure of other income [text block]"`.
    After normalisation these must resolve as an exact match, not a
    fuzzy match below threshold."""
    entries = _entries([(66, "Disclosure of other income [text block]")])
    result = _resolve_row(entries, "Disclosure of other income")
    assert result is not None, (
        "Bare label lost to [text block] suffix — the silent drop from "
        "run #105 is regressing."
    )
    row, chosen, score = result
    assert row == 66
    # Exact after normalisation → score 1.0 (not fuzzy).
    assert score == 1.0, f"Expected exact match post-normalise, got {score}"


def test_resolve_row_matches_text_block_payload_to_bare_row():
    """Symmetric case — payload carries the suffix, template is bare
    (MFRS layout). Must still resolve cleanly so pipelines that feed
    the suffix back in from a prior pass don't regress."""
    entries = _entries([(66, "Disclosure of other income")])
    result = _resolve_row(entries, "Disclosure of other income [text block]")
    assert result is not None
    assert result[0] == 66
    assert result[2] == 1.0


def test_resolve_row_strips_abstract_suffix():
    """`[abstract]` is another SSM ReportingLabel type suffix; it appears
    on section headers in MPERS templates. Same normalisation rule must
    apply so a label with the suffix can be looked up by its bare form."""
    entries = _entries([
        (3, "Disclosure of notes and other explanatory information [abstract]"),
    ])
    result = _resolve_row(
        entries, "Disclosure of notes and other explanatory information"
    )
    assert result is not None
    assert result[0] == 3


def test_resolve_row_strips_axis_and_member_suffixes():
    """`[axis]` and `[member]` appear on taxonomy dimensional rows.
    Grouped together so one parametric-style test covers the full
    SSM type suffix vocabulary we currently know about."""
    for suffix in ("[axis]", "[member]", "[table]"):
        entries = _entries([(5, f"Disclosure of component {suffix}")])
        result = _resolve_row(entries, "Disclosure of component")
        assert result is not None, (
            f"Suffix {suffix!r} not stripped by _normalize"
        )
        assert result[0] == 5


def test_resolve_row_does_not_match_across_concepts():
    """Collision guard: stripping the suffix must not turn semantically
    different labels into matches. 'other operating expenses' should
    NOT match 'other comprehensive income' even though both strings
    share 'other' — the fuzzy threshold still governs cross-concept
    matching, it's only suffix equivalence we're relaxing."""
    entries = _entries([
        (63, "Disclosure of other comprehensive income [text block]"),
    ])
    result = _resolve_row(entries, "Disclosure of other operating expenses")
    assert result is None, (
        "Suffix stripping over-reached: cross-concept match accepted "
        "when it should be rejected below the fuzzy threshold."
    )


def test_normalize_preserves_leading_asterisk_strip():
    """Pre-existing contract: `_normalize` strips a leading asterisk
    (our template marker for required rows). Suffix stripping must not
    regress this behaviour."""
    assert _normalize("* Disclosure of X [text block]") == "disclosure of x"


def test_normalize_idempotent():
    """Running _normalize twice must equal running it once — defensive
    property so callers can pre-normalise without corrupting the value."""
    label = "  * Disclosure of X [text block] "
    once = _normalize(label)
    twice = _normalize(once)
    assert once == twice
