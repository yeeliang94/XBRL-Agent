"""Phase 1a Step 2 — text-PDF face-structure parser.

The two test PDFs in `data/` (FINCO, Oriental) are both fully scanned, so
PyMuPDF returns no text from their face pages — the regex path can't be
exercised against them. We instead use synthetic SOFP/SOPL text that
mirrors how a text-based Malaysian AFS renders through PyMuPDF: line items
followed by "Note N" cross-references, with section headers ("Non-current
assets" / "Current assets") on their own lines.

The vision path (Phase 1a Step 5) covers the scanned-PDF case end-to-end.
"""
from __future__ import annotations

from scout.face_structure import read_face_structure


# Representative SOFP face-page text. Mirrors what a text-PDF Malaysian
# annual report yields from PyMuPDF: column headers on their own line,
# section headers separating asset/liability blocks, line items followed
# by "Note N" then numeric values.
_SOFP_FACE_TEXT = """\
Statement of Financial Position
As at 31 December 2022

2022
2021
RM '000
RM '000

ASSETS

Non-current assets
Property, plant and equipment   Note 4   12,500   11,200
Right-of-use assets   Note 5   3,400   3,800
Intangible assets   Note 6   1,100   1,250

Current assets
Trade receivables   Note 7   8,900   7,400
Cash and bank balances   Note 8   2,100   1,950
Total current assets   11,000   9,350

Total assets   28,000   25,600

EQUITY AND LIABILITIES

Equity
Share capital   Note 9   10,000   10,000
Retained earnings   8,500   7,200

Non-current liabilities
Lease liabilities   Note 10   3,000   3,400

Current liabilities
Trade payables   Note 11   6,500   5,000
"""


# Representative SOPL face-page text — function-of-expense variant.
_SOPL_FACE_TEXT = """\
Statement of Profit or Loss
For the year ended 31 December 2022

2022
2021
RM '000
RM '000

Revenue   Note 12   45,000   42,000
Cost of sales   Note 13   (28,000)   (26,000)
Gross profit   17,000   16,000

Other income   Note 14   500   320
Distribution costs   (3,200)   (3,000)
Administrative expenses   Note 15   (5,800)   (5,500)
Finance costs   Note 16   (450)   (520)

Profit before tax   8,050   7,300
Tax expense   Note 17   (2,000)   (1,800)
"""


class TestSofpFaceParse:
    def test_returns_line_refs_for_every_noted_item(self):
        refs = read_face_structure(_SOFP_FACE_TEXT)
        # Sanity: at least the items we know are present should show up
        labels_by_note = {r.note_num: r.label for r in refs}
        assert 4 in labels_by_note
        assert "Property, plant and equipment" in labels_by_note[4]
        assert labels_by_note.get(7) == "Trade receivables"
        assert labels_by_note.get(8) == "Cash and bank balances"
        assert labels_by_note.get(11) == "Trade payables"

    def test_section_context_is_attached(self):
        refs = read_face_structure(_SOFP_FACE_TEXT)
        by_note = {r.note_num: r.section for r in refs}
        # PPE sits under "Non-current assets"
        assert by_note[4] == "non-current assets"
        # Trade receivables sits under "Current assets"
        assert by_note[7] == "current assets"
        # Share capital sits under "Equity"
        assert by_note[9] == "equity"
        # Trade payables sits under "Current liabilities"
        assert by_note[11] == "current liabilities"

    def test_total_lines_are_not_section_headers(self):
        # "Total current assets" and "Total assets" must not switch the
        # active section — they're terminal lines, not new blocks.
        refs = read_face_structure(_SOFP_FACE_TEXT)
        # Share capital sits AFTER "Total assets" and "EQUITY AND
        # LIABILITIES" — it must be classified under "equity", not
        # under "total assets" or similar.
        share_capital = next(r for r in refs if r.note_num == 9)
        assert share_capital.section == "equity"

    def test_unnoted_lines_are_skipped(self):
        # "Retained earnings" has no Note reference — it should be absent
        # from the deterministic parser's output (vision path may catch
        # it, but regex stays conservative).
        refs = read_face_structure(_SOFP_FACE_TEXT)
        labels = {r.label for r in refs}
        assert "Retained earnings" not in labels


class TestSoplFaceParse:
    def test_captures_typical_sopl_lines(self):
        refs = read_face_structure(_SOPL_FACE_TEXT)
        by_note = {r.note_num: r.label for r in refs}
        assert by_note[12] == "Revenue"
        assert by_note[13] == "Cost of sales"
        assert by_note[16] == "Finance costs"
        assert by_note[17] == "Tax expense"


class TestEdgeCases:
    def test_empty_input_returns_empty_list(self):
        # The explicit hand-off contract to the vision path.
        assert read_face_structure("") == []
        assert read_face_structure("   \n  \n  ") == []

    def test_no_note_references_returns_empty_list(self):
        text = "Statement of Profit or Loss\nRevenue   10,000\nCost of sales   (7,000)\n"
        assert read_face_structure(text) == []

    def test_lowercase_note_reference_still_matches(self):
        # Auditors occasionally use "note 4" lowercase.
        text = "Property, plant and equipment   note 4   12,500\n"
        refs = read_face_structure(text)
        assert len(refs) == 1
        assert refs[0].note_num == 4

    def test_pure_numeric_lines_ignored(self):
        # "2022" / "RM '000" rows shouldn't produce phantom FaceLineRefs.
        text = "2022\n2021\nRM '000\nRM '000\n"
        assert read_face_structure(text) == []
