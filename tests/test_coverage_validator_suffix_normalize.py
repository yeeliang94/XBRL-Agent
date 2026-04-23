"""Phase 2 MPERS hardening — the coverage validator's label comparator
must mirror the writer's taxonomy suffix stripping so receipt labels
stay equivalent to payload_sink labels across the suffix difference.

Red tests for `docs/PLAN-mpers-notes-hardening.md` Phase 2. The writer
side is tested in `test_notes_writer_suffix_normalize.py`; this file
covers the coverage.py side.

`notes/coverage.py` explicitly documents that its `_normalize_label`
mirrors `notes.writer._normalize`. Breaking that invariant makes the
fuzzy-accepted payload look different from the receipt-claimed label
and rejects a valid receipt.
"""
from __future__ import annotations

from notes.coverage import CoverageReceipt, _normalize_label


# ---------------------------------------------------------------------------
# 2.1 coverage — taxonomy suffix stripping in _normalize_label
# ---------------------------------------------------------------------------

def test_normalize_label_strips_text_block_suffix():
    assert _normalize_label("Disclosure of other income [text block]") == (
        "disclosure of other income"
    )


def test_normalize_label_strips_abstract_suffix():
    assert _normalize_label("Disclosure of X [abstract]") == "disclosure of x"


def test_normalize_label_strips_axis_member_table_suffixes():
    for suffix in ("[axis]", "[member]", "[table]"):
        assert _normalize_label(f"Disclosure of Y {suffix}") == "disclosure of y"


def test_normalize_label_agrees_with_writer_normalize():
    """Cross-layer invariant: coverage._normalize_label ≡ writer._normalize.
    If this drifts, the validator will reject labels the writer accepts.
    """
    from notes.writer import _normalize
    samples = [
        "Disclosure of other income [text block]",
        "* Disclosure of financial instruments [abstract]",
        "Disclosure of trade and other payables",
    ]
    for s in samples:
        assert _normalize_label(s) == _normalize(s), (
            f"Normalisers out of sync for {s!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end: receipt claiming a bare label must match a payload_sink
# whose label carries the [text block] suffix.
# ---------------------------------------------------------------------------

def test_validator_accepts_bare_label_claim_against_text_block_sink():
    """The sub-agent writes a payload with the MPERS-style label
    (`[text block]` suffix); its receipt claims a bare label. Before
    Phase 2 this was a validator rejection. After Phase 2 the labels
    are equivalent and the receipt passes."""
    receipt = CoverageReceipt.from_json(
        '[{"note_num": 8, "action": "written", '
        '"row_labels": ["Disclosure of other income"]}]'
    )
    # Sink uses the MPERS-suffixed label (writer keeps the original
    # string; normalisation only runs in the comparator).
    sink = {8: {"Disclosure of other income [text block]"}}
    errors = receipt.validate(batch_note_nums=[8], written_row_labels=sink)
    assert errors == [], (
        "Coverage validator still rejects equivalent labels across "
        "the [text block] suffix — Phase 2 regression."
    )


def test_validator_rejects_mismatched_concepts_even_with_suffix_normalization():
    """Guardrail: stripping the suffix must not make cross-concept
    claims pass. The validator should still flag a receipt claiming
    'other income' when the sink only saw 'other comprehensive income'."""
    receipt = CoverageReceipt.from_json(
        '[{"note_num": 1, "action": "written", '
        '"row_labels": ["Disclosure of other income"]}]'
    )
    sink = {1: {"Disclosure of other comprehensive income [text block]"}}
    errors = receipt.validate(batch_note_nums=[1], written_row_labels=sink)
    assert errors, (
        "Validator wrongly accepted cross-concept claim after suffix "
        "stripping — suffix rule over-reached."
    )
