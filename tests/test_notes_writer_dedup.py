"""Phase 2 (post-FINCO-2021 audit) — writer aggregation dedup.

`notes.writer._combine_payloads` merges multiple payloads that target the
same template row (common for Sheet 12 sub-agents that each discover a
piece of the same disclosure). The merge used to concatenate evidence
verbatim, producing artefacts like:

    "Pages 34-36, Note 13 (Credit risk); Pages 34-36, Note 13 (Credit risk)"

in col D, and repeated integers in source_pages. These tests pin the
dedup contract on both fields so a later refactor can't silently
regress it.
"""
from __future__ import annotations

from notes.payload import NotesPayload
from notes.writer import _combine_payloads


def _p(label: str, content: str, evidence: str, pages: list[int]) -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content=content,
        evidence=evidence,
        source_pages=pages,
    )


def test_combine_dedups_identical_evidence_fragments():
    """Two sub-agents contributing identical evidence strings should
    produce ONE fragment in the combined evidence — not "X; X"."""
    merged = _combine_payloads([
        _p("Disclosure of credit risk", "Part A", "Pages 34-36, Note 13 (Credit risk)", [34, 35, 36]),
        _p("Disclosure of credit risk", "Part B", "Pages 34-36, Note 13 (Credit risk)", [34, 35, 36]),
    ])
    # Single fragment only.
    assert merged.evidence == "Pages 34-36, Note 13 (Credit risk)"
    # Both content chunks still preserved.
    assert "Part A" in merged.content and "Part B" in merged.content
    # Pages deduped.
    assert merged.source_pages == [34, 35, 36]


def test_combine_dedups_evidence_case_insensitively():
    """Same citation with different capitalisation must still dedup —
    'Page 27' and 'page 27' should collapse to one fragment."""
    merged = _combine_payloads([
        _p("Row", "x", "Page 27, Note 2.5(g)", [27]),
        _p("Row", "y", "page 27, note 2.5(g)", [27]),
    ])
    parts = [s.strip() for s in merged.evidence.split(";")]
    assert len(parts) == 1


def test_combine_splits_already_joined_evidence_before_dedup():
    """One payload's `evidence` might itself contain "A; B" (when a
    sub-coordinator already joined within a batch). Splitting before
    dedup is what lets later merges catch duplicates that cross batch
    boundaries."""
    merged = _combine_payloads([
        _p("Row", "x", "Pages 34-36, Note 13; Pages 35-36, Note 13", [34, 35, 36]),
        _p("Row", "y", "Pages 35-36, Note 13", [35, 36]),
    ])
    parts = [s.strip() for s in merged.evidence.split(";")]
    # Both distinct citations survive; the duplicate (second payload's
    # fragment) does not reappear.
    assert "Pages 34-36, Note 13" in parts
    assert "Pages 35-36, Note 13" in parts
    assert len(parts) == 2


def test_combine_preserves_first_seen_order_for_evidence():
    """Dedup must not reorder surviving fragments — stable order is what
    keeps re-runs deterministic."""
    merged = _combine_payloads([
        _p("Row", "x", "Page 18, Note 1", [18]),
        _p("Row", "y", "Page 27, Note 2.5(g)", [27]),
        _p("Row", "z", "Page 18, Note 1", [18]),  # dup of first
    ])
    parts = [s.strip() for s in merged.evidence.split(";")]
    # "Page 18" must come first (it appeared first); "Page 27" second.
    assert parts == ["Page 18, Note 1", "Page 27, Note 2.5(g)"]


def test_combine_dedups_source_pages_and_preserves_order():
    """source_pages dedup was already in place — pin it so it stays in
    place after the Phase 2 refactor of the surrounding evidence block."""
    merged = _combine_payloads([
        _p("Row", "x", "A", [18, 19]),
        _p("Row", "y", "B", [19, 18]),  # duplicates + reordered
    ])
    # First-seen order: 18, 19 (from payload 1).
    assert merged.source_pages == [18, 19]


def test_combine_single_clean_payload_is_passthrough():
    """Fast path: a single payload with no ';' in evidence and nothing
    to dedup returns the exact same object (cheap short-circuit)."""
    p = _p("Row", "x", "Page 18", [18])
    merged = _combine_payloads([p])
    assert merged is p


def test_combine_single_payload_with_joined_evidence_is_deduped():
    """Peer-review #5: a single payload whose own evidence carries a
    duplicate fragment (e.g. the sub-agent emitted 'Page 18; Page 18')
    must still be cleaned — skipping dedup on len==1 preserved exactly
    this artefact on the FINCO 2021 run."""
    p = _p("Row", "x", "Page 18; Page 18", [18, 18])
    merged = _combine_payloads([p])
    assert merged.evidence == "Page 18"
    # source_pages dedup already ran as part of the full merge — pin it.
    assert merged.source_pages == [18]
