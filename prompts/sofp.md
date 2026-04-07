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
3. For each face line item that has a note reference (e.g. "Note 4", "Note 5"):
   - View the note pages to get the detailed breakdown.
   - Map each note line item to its sub-sheet field.
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

=== CRITICAL RULES ===

- ALWAYS fill the sub-sheet for every breakdown you find in the notes. Missing sub-sheet
  values = wrong totals because the main sheet formulas depend on sub-sheet data.
- When a note shows a breakdown (e.g. "Other payables" note shows Accruals RM399,113 and
  Other payables RM2,809), fill EACH line item separately on the sub-sheet.
- "Accruals" in the template means ONLY the accruals line. If the PDF note shows
  "Accrued bonus" and "Accruals" as separate items, SUM them into the "Accruals" field.
  Do NOT put accrued bonus into "Other current non-trade payables".
- "Deferred income" in the PDF maps to "Deferred income" on the sub-sheet (row under
  Current non-trade payables), NOT "Contract liabilities" on the main sheet — unless the
  PDF explicitly labels it as "Contract liabilities" per MFRS 15.
- "Deposits" in receivable notes → "Deposits" under Current non-trade receivables.
  Do NOT put deposits into "Other current non-trade receivables".
- Always include "section" for ambiguous labels (current vs non-current).