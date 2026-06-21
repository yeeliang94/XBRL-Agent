"""Face-statement citation hygiene: the printed-folio↔PDF-page offset block.

Notes agents already get a page-offset block so they cite PDF page indices;
face-statement agents did NOT, so they cited printed folios. When the two
disagree (offset > 0) the `notes_consistency` cross-check sees disjoint pages
and raises spurious warnings. This pins that the face prompt now renders the
same offset guidance when scout measured a non-zero offset (threaded via
scout_context, like the prior-year advisory).
"""
from __future__ import annotations

from prompts import render_prompt
from statement_types import StatementType


def _render(page_offset):
    ctx = {"entity_name": "ACME", "scale_unit": "thousands"}
    if page_offset is not None:
        ctx["page_offset"] = page_offset
    return render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        filing_level="company",
        filing_standard="mfrs",
        scout_context=ctx,
    )


def test_offset_block_rendered_when_offset_present():
    rendered = _render(2)
    assert "PDF vs PRINTED PAGE OFFSET" in rendered
    assert "offset of +2" in rendered
    # Cites PDF page, not folio: viewing PDF 12 with footer '10' → cite 'Page 12'.
    assert "Page 12" in rendered


def test_offset_block_absent_when_offset_zero_or_missing():
    assert "PDF vs PRINTED PAGE OFFSET" not in _render(0)
    assert "PDF vs PRINTED PAGE OFFSET" not in _render(None)


def test_offset_block_absent_for_negative_offset():
    assert "PDF vs PRINTED PAGE OFFSET" not in _render(-3)
