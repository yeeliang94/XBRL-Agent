=== STATEMENT: SOFP (Statement of Financial Position) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

The MBRS template has TWO sheets that MUST BOTH be filled:

1. **SOFP-CuNonCu** (main sheet) — Face of the Statement of Financial Position.
   Contains high-level line items. Many cells are FORMULAS that pull from the sub-sheet.
   Only fill DATA-ENTRY cells here (non-formula cells like "Right-of-use assets",
   "Retained earnings", "Lease liabilities", "Contract liabilities").

2. **SOFP-Sub-CuNonCu** (sub-sheet) — Detailed breakdowns of each main-sheet line item.
   This is where MOST of your data should go. The sub-sheet has granular fields like:
   - "Office equipment, fixture and fittings" under Property, plant and equipment
   - "Trade receivables" under Current trade receivables
   - "Deposits" under Current non-trade receivables
   - "Balances with Licensed Banks" under Cash
   - "Accruals", "Deferred income", "Other current non-trade payables" under Current non-trade payables

   The main sheet formulas automatically sum these sub-sheet values. If you only fill
   the main sheet, the formulas will OVERWRITE your values when opened in Excel.

=== STRATEGY ===

IMPORTANT: Fill the SUB-SHEET FIRST. The main sheet has formulas that pull totals from
the sub-sheet automatically. Only fill non-formula data-entry cells on the main sheet
(e.g. "Right-of-use assets", "Retained earnings", "Lease liabilities", "Contract liabilities").

1. Call read_template() to understand the template structure and which cells need data.
2. View the SOFP face page to see the statement.
3. For every face-sheet line that cites a note reference (e.g. "Note 4", "Note 5"):
   - a. View the note pages to read the breakdown.
   - b. Look at the sub-sheet's field list under the matching section (the
     read_template() output lists every row label under each section).
   - c. For each note breakdown line, check: is there a matching sub-sheet
     field? If YES, write that note line's value to that sub-sheet field.
   - d. Note lines that don't match a sub-sheet field roll into the nearest
     broader field (or, if the template is fully coarser, roll up into a
     single sub-sheet line). Do NOT invent template rows to match note
     granularity.
   - e. Sub-sheet fields with no matching note line are left empty. Never
     fabricate a breakdown.
4. For face line items WITHOUT note references or that are direct data-entry on the main
   sheet, fill them on the main sheet.
5. Call fill_workbook() with ALL field mappings. Prioritise sub-sheet fields:
   - Sub-sheet example: {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Trade receivables",
     "section": "current trade receivables", "col": 2, "value": 384375, ...}
   - Main-sheet example: {"sheet": "SOFP-CuNonCu", "field_label": "Retained earnings",
     "section": "equity", "col": 2, "value": 2543264, ...}
6. Call verify_totals() to check the balance sheet balances.
7. If totals don't balance, identify which section is wrong, re-examine notes, and
   call fill_workbook() again with corrections.
8. Call save_result() when totals balance.

=== FAILURE MODE TO AVOID ===

The asymmetric failure is: the sub-sheet has a granular field that matches a
breakdown line in the note, but you write the combined lump sum to the face
sheet. The face-sheet formula then overwrites your lump sum with the (empty)
sub-sheet total, and the filed return is wrong.

Correct outcome: the values you see in the notes end up in sub-sheet fields
that match their labels. When the template is coarser than the note, rolling
up is the right move. When the template is granular, split the note lines.
Let the template drive, not the note.

=== WORKED EXAMPLES ===

**Template-granular case (split the note):** The "Other payables" note shows
Accruals RM399,113 + Other payables RM2,809. The sub-sheet has both
"Accruals" and "Other current non-trade payables" under the same section →
write each note line to its matching field. Two sub-sheet payloads.

**Note-granular case (roll up into template):** The "Trade receivables" note
shows Trade receivables – third parties RM320,000 + Trade receivables –
related companies RM64,375. The sub-sheet has only one "Trade receivables"
field under Current trade receivables → sum the two note lines to RM384,375
and write that one value to "Trade receivables". One sub-sheet payload.
Do NOT invent "Trade receivables – third parties" as a new row.

=== CRITICAL RULES ===

- ALWAYS fill the sub-sheet for every breakdown the template exposes. When
  a matching sub-sheet field exists, that's where the note line belongs —
  not on the face sheet as a lump sum.
- When a note's breakdown is finer than the sub-sheet, roll up into the
  coarsest matching field. Never fabricate template rows.
- "Accruals" in the template means ONLY the accruals line. If the PDF note shows
  "Accrued bonus" and "Accruals" as separate items, SUM them into the "Accruals" field.
  Do NOT put accrued bonus into "Other current non-trade payables".
- "Deferred income" in the PDF maps to "Deferred income" on the sub-sheet (row under
  Current non-trade payables), NOT "Contract liabilities" on the main sheet — unless the
  PDF explicitly labels it as "Contract liabilities" per MFRS 15.
- "Deposits" in receivable notes → "Deposits" under Current non-trade receivables.
  Do NOT put deposits into "Other current non-trade receivables".
- Always include "section" for ambiguous labels (current vs non-current).