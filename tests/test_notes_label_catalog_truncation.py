"""Label-catalog truncation footer (peer-review S-6).

`notes/agent.py:_render_label_catalog` caps the seeded row-label block at
`_LABEL_CATALOG_MAX_ROWS` entries. When a template has more rows than the
cap, the prompt must include a trailing note telling the agent there are
additional labels available via `read_template` — otherwise the agent
sees a truncated catalog and silently assumes the labels shown are the
complete set, which would make it skip cells it should have filled.

These tests pin that contract so a refactor that drops the truncation
footer or changes its phrasing in a way the agent can't recognise is
caught before a production run.
"""
from __future__ import annotations

from notes.agent import _LABEL_CATALOG_MAX_ROWS, _render_label_catalog


def test_empty_catalog_returns_none():
    """No labels → no block at all (keeps prompt shape compatible with
    the pre-Phase-3 layout when the caller doesn't seed a catalog)."""
    assert _render_label_catalog([]) is None


def test_under_cap_shows_every_label_without_truncation_footer():
    labels = [f"Disclosure of item {i}" for i in range(5)]
    block = _render_label_catalog(labels)
    assert block is not None
    for label in labels:
        assert label in block
    # The truncation-style footer must NOT appear when every label fit.
    assert "more row" not in block


def test_over_cap_emits_truncation_footer_with_count():
    """Exercise the overflow path: construct enough labels to go past
    the cap and assert the footer surfaces the count of dropped rows.

    We pin the overflow number because the test's value is that the
    agent can tell from the prompt alone that more labels exist — a
    footer that says "… and more row(s)" without a number still
    satisfies the contract, but the numeric form is what we ship today.
    """
    total = _LABEL_CATALOG_MAX_ROWS + 7
    labels = [f"Disclosure of item {i}" for i in range(total)]
    block = _render_label_catalog(labels)
    assert block is not None
    # Shown label count equals the cap.
    shown_count = sum(
        1 for line in block.splitlines() if line.startswith("  - ")
    )
    assert shown_count == _LABEL_CATALOG_MAX_ROWS

    # Footer references the dropped count so the agent knows to call
    # `read_template` for the remainder.
    assert "and 7 more row" in block
    assert "read_template" in block


def test_over_cap_keeps_first_labels_stable():
    """Truncation must keep the first N labels (caller-supplied order),
    not sort or shuffle. Ordering stability is what lets the seeding
    strategy put the most common labels near the top of the list."""
    labels = [f"Label-{i:04d}" for i in range(_LABEL_CATALOG_MAX_ROWS + 20)]
    block = _render_label_catalog(labels)
    assert block is not None
    # First label must appear; last label that fits (index cap-1) must
    # appear; first dropped label (index cap) must NOT appear.
    assert labels[0] in block
    assert labels[_LABEL_CATALOG_MAX_ROWS - 1] in block
    assert labels[_LABEL_CATALOG_MAX_ROWS] not in block
