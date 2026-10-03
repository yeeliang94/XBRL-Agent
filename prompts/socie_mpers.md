=== STATEMENT: SOCIE (Statement of Changes in Equity) — MPERS Default ({{VARIANT}}) ===

=== TEMPLATE STRUCTURE (MPERS) ===

The MPERS SOCIE template is an equity-component matrix layout. Rows are equity
movements. Columns retain each source equity component separately:

- B (2): Issued capital
- C (3): Retained earnings
- D (4): Treasury shares
- E (5): Capital reserve
- F (6): Foreign currency translation reserve
- G (7): Revaluation surplus
- H (8): Other non-distributable reserves
- I (9): Sub-total of non-distributable reserves — formula
- J (10): Other distributable reserves
- K (11): Reserves — formula
- L (12): Equity attributable to owners of parent — formula
- M (13): Equity, other components
- N (14): Non-controlling interests
- O (15): Total — formula
- P (16): Source / evidence

Company filings contain two stacked period blocks. Group filings contain four
stacked blocks, labelled Group - Current period, Group - Prior period,
Company - Current period and Company - Prior period. Read the actual movement
row numbers and block headings from read_template() before writing.

=== STRATEGY ===

1. Call read_template() and view the source Statement of Changes in Equity.
2. Identify each source equity component and its matching component column.
   Keep issued capital, retained earnings, reserves and non-controlling interests
   separate. Never assign an aggregate total to a component without source evidence.
3. Write explicit row and col coordinates at each source movement/component
   intersection. Example: profit (loss) attributable to retained earnings uses
   col=3 at the profit row returned by read_template(). The field_label alone
   does not identify a component or a period block.
4. Enter source opening, restated opening and closing balances for every
   reported component. Enter accounting-policy adjustments when disclosed.
   The source closing balance remains an independent reconciliation control.
5. Fill the source's current and comparative blocks for each reported entity
   scope. A filing explicitly marked as first financial statements uses CY only.
6. Call write_facts(), verify_totals() (status-only), then save_result().

=== CRITICAL RULES (MPERS) ===

- Never write formula columns I, K, L or O or formula-owned movement rows.
  Their totals derive from the component facts and taxonomy relationships.
- Leave unreported movements blank. Do not invent a component split or a
  balancing residual. If the source supplies only aggregate equity without
  enough component evidence, preserve the unresolved issue for review.
- Do not apply the SOPL sign convention to equity movements. Dividends paid
  are entered as positive magnitudes because the formula subtracts the dividends row.
  Other equity movements follow their source/formula sign. Closing total equity
  reconciles to SOFP; retain any disagreement for review.
- Profit attributable to owners belongs to retained earnings; reported NCI
  profit belongs to column N. Do not put consolidated total profit in both.
- Preserve each source period and entity scope. PY closing must agree with CY
  opening when both periods are presented. Read actual block rows from the
  template; never reuse MFRS reserve columns or row coordinates.
