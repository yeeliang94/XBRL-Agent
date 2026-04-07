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
6. If the entity has NO OCI items (common for simple companies), only the Profit/(loss)
   row needs to be filled. All OCI rows stay blank (formula subtotals will be zero).
7. Call fill_workbook(), verify_totals(), and save_result().

=== CRITICAL RULES ===

- Profit/(loss) is a DATA-ENTRY cell, not a formula. It must match SOPL exactly.
- OCI losses are entered as NEGATIVE values (unlike SOPL expenses which are positive).
- Total comprehensive income must equal the TCI row in SOCIE — cross-check this.
- For BeforeTax: enter gross OCI amounts AND the tax effect separately. Do not net them.
- For NetOfTax: enter amounts already net of tax. No separate tax rows.
- Many entities have zero OCI — this is normal. Do not fabricate OCI items.
- Attributable splits (owners of parent vs NCI) only apply to group accounts.