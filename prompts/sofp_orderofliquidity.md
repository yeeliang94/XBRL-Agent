=== STATEMENT: SOFP (Statement of Financial Position) — OrderOfLiquidity ===

=== TEMPLATE STRUCTURE ===

The MBRS Order-of-Liquidity template has TWO sheets:

1. **SOFP-OrdOfLiq** (main sheet) — Face of the Statement of Financial Position.
   Presents assets and liabilities ranked by liquidity WITHOUT current/non-current splits.
   Contains high-level line items. Unlike the CuNonCu variant, this sheet has NO
   cross-sheet formula references — it is standalone. Writing values here will NOT be
   overwritten by sub-sheet formulas.

2. **SOFP-Sub-OrdOfLiq** (sub-sheet) — Detailed breakdowns of each main-sheet line item.
   Provides granular fields for note breakdowns. The main sheet does NOT auto-pull from
   this sheet, so filling it is for completeness and auditability, not for formula rollup.

=== STRATEGY ===

Both sheets can be filled independently. Fill main sheet for face values, sub-sheet for
note breakdowns where available.

1. Call read_template() to understand the template structure and which cells need data.
2. View the SOFP face page to see the statement.
3. For each face line item that has a note reference (e.g. "Note 4", "Note 5"):
   - View the note pages to get the detailed breakdown.
   - Map each note line item to its sub-sheet field on SOFP-Sub-OrdOfLiq.
4. Fill the main sheet (SOFP-OrdOfLiq) with face-level values — these are standalone
   data-entry cells that will NOT be overwritten.
5. Call fill_workbook() with ALL field mappings:
   - Main-sheet example: {"sheet": "SOFP-OrdOfLiq", "field_label": "Cash and cash equivalents",
     "section": "assets", "col": 2, "value": 2551004, ...}
   - Sub-sheet example: {"sheet": "SOFP-Sub-OrdOfLiq", "field_label": "Trade receivables",
     "section": "trade and other receivables", "col": 2, "value": 384375, ...}
6. Call verify_totals() to check the balance sheet balances.
7. If totals don't balance, identify which section is wrong, re-examine notes, and
   call fill_workbook() again with corrections.
8. Call save_result() when totals balance.

=== CRITICAL RULES ===

- **No current/non-current distinction** — this variant does NOT split into current and
  non-current sections. Lease liabilities, receivables, etc. are single combined amounts.
- Section hints are simpler: just "assets", "equity", "liabilities".
- **Main sheet is standalone** — unlike CuNonCu, writing to the main sheet does NOT get
  overwritten by sub-sheet formulas. You can safely write face values directly.
- When a note shows a breakdown (e.g. "Other payables" note shows Accruals RM399,113 and
  Other payables RM2,809), fill EACH line item separately on the sub-sheet.
- "Accumulated fund" in the PDF = "Retained earnings" in the template.
- "Deferred income" in the PDF maps to "Contract liabilities" on the main sheet, or
  "Deferred income" on the sub-sheet if available.
- Always include "section" for disambiguation (assets/equity/liabilities).
