=== STATEMENT: SOCI (Statement of Comprehensive Income) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

Single sheet with OCI (Other Comprehensive Income) items. The statement starts with
Profit/(loss) for the year (from SOPL), then lists OCI items, and arrives at Total
Comprehensive Income (TCI).

OCI items are grouped into:
- **Items that will NOT be reclassified to P&L:** revaluation gains, remeasurement of
  defined benefit plans, FVOCI equity instruments
- **Items that MAY be reclassified to P&L:** foreign exchange translation differences,
  cash flow hedges, hedges of net investment, FVOCI debt instruments

For **BeforeTax** variant: OCI items are shown at gross amounts. A separate section shows
income tax on each OCI component. More rows (48 vs 42).

For **NetOfTax** variant: OCI items are shown net of their tax effects. No separate tax
section. Simpler layout.

=== STRATEGY ===

1. Call read_template() to see which OCI categories exist.
2. View the comprehensive income statement page (often immediately after the P&L, or
   combined with it as "Statement of Profit or Loss and Other Comprehensive Income").
3. Enter the Profit/(loss) for the year as the first data row — this MUST match the
   SOPL bottom line exactly.
4. For each OCI item disclosed, map to the correct category and enter the value.
5. For BeforeTax variant: also fill the tax-on-OCI rows at the bottom.
6. If the entity has NO OCI items (common for simple companies), fill the Profit/(loss)
   row and the required Company attribution described below. All OCI rows stay blank (formula subtotals will be zero).

=== CRITICAL RULES ===

- Profit/(loss) is a DATA-ENTRY cell, not a formula. It must match SOPL exactly.
- OCI losses are entered as NEGATIVE values (unlike SOPL expenses which are positive).
- Total comprehensive income (the TCI row) auto-computes from the values you
  enter. You CANNOT cross-check it against SOCIE here — you only see the SOCI.
  A later cross-check does that; your job is to enter the profit row and each
  OCI item correctly from the PDF so the computed TCI is right.
- For BeforeTax: enter gross OCI amounts AND the tax effect separately. Do not net them.
- For NetOfTax: enter amounts already net of tax. No separate tax rows.
- Many entities have zero OCI — this is normal. Do not fabricate OCI items.
- On Company filings, assign the full total comprehensive income to the live
  owners-attribution leaf for CY and PY independently, even when the source
  prints no separate attribution split. Cite the Company statement and explain
  that the full Company TCI belongs to its owners. Leave NCI blank when it is
  not disclosed; do not fabricate a zero or an NCI allocation.
- On Group filings, use the source-disclosed owners/NCI split for each entity
  scope. Never copy a Group split into Company columns or infer a Group split.
- Verify both TCI totals agree. Blank Company attribution is incomplete, not
  evidence that the attribution check is inapplicable.