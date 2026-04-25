=== STATEMENT: SOPL (Statement of Profit or Loss) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

The SOPL template has two sheets:

1. **Main sheet** — Face of the income statement with high-level P&L line items:
   Revenue, Cost of sales, Gross profit, Operating expenses by function/nature,
   Finance income/costs, Tax expense, and Profit/Loss attributable splits.

2. **Analysis sub-sheet** — Detailed breakdowns of revenue (by type: goods, services,
   fees, etc.), cost of sales, other income, other expenses, employee benefits,
   director remuneration, and finance income.

For the **Function** variant: expenses are classified by function (cost of sales,
distribution, admin, research). The main sheet has some cross-sheet formula references
to the Analysis sub-sheet for Revenue, CoS, Other income, Other expenses, and Finance income.

For the **Nature** variant: expenses are classified by nature (raw materials, employee
benefits, depreciation). The main sheet is mostly self-contained with no cross-sheet refs.

=== STRATEGY ===

1. Call read_template() to understand which cells are data-entry vs. formula.
2. View the income statement / profit or loss page.
3. For each face-sheet line that cites a note reference:
   - a. View the note pages to read the breakdown.
   - b. Look at the Analysis sub-sheet's field list under the matching
     section (Revenue by type, Cost of sales components, Other income,
     Finance income, Director remuneration, Employee benefits, etc.).
   - c. For each note breakdown line, check: is there a matching Analysis
     sub-sheet field? If YES, write that note line's value to that field.
   - d. Note lines with no matching sub-sheet field roll into the nearest
     broader field. Never invent template rows.
   - e. Sub-sheet fields with no matching note line stay empty.
4. Fill the Analysis sub-sheet FIRST with revenue and expense breakdowns from notes.
5. Fill remaining main-sheet data-entry cells (expenses by function, tax, EPS, etc.).
6. Call fill_workbook() with all mappings.
7. Call verify_totals() to report verification status (balance checks are SOFP-only for now).
8. Call save_result() when complete.

=== FAILURE MODE TO AVOID ===

The asymmetric failure is: the Revenue note breaks down revenue by type
(goods / services / freight), matching Analysis sub-sheet fields exist, but
you write the combined total as a single Revenue line on the face sheet.
The face-sheet Revenue cell is a formula that pulls from the Analysis
sub-sheet — a missing Analysis breakdown leaves the face-sheet Revenue at
zero (or the formula overwrites your lump sum with the sub-sheet total).

Correct outcome: revenue and expense note breakdowns land in the Analysis
sub-sheet's matching fields. The template controls the level of granularity;
when it's coarser than the note, roll up.

=== WORKED EXAMPLES ===

**Template-granular case (split the note):** Revenue note shows Sale of
goods RM5,000,000 + Services RM3,000,000 + Fees RM1,000,000. The Analysis
sub-sheet has matching "Revenue from sale of goods", "Revenue from
rendering of services", and "Revenue from fees" fields → write three
separate Analysis payloads.

**Note-granular case (roll up):** Employee-benefits note shows Wages and
salaries RM800k + Bonuses RM50k + Training RM10k. The Analysis sub-sheet
has only one "Wages, salaries and bonuses" field → sum the three note
lines to RM860k and write to that one field. Training may roll into the
same line if no separate "Training" field exists.

=== CRITICAL RULES ===

- Expenses are entered as POSITIVE values in the template. The formulas handle sign
  conventions. Do not enter negative values for expenses.
- Loss-labelled expense rows are also POSITIVE magnitudes when they are P&L
  charges. Examples: "Foreign exchange loss", "Impairment loss on trade
  receivables", "Expected credit loss allowance", "Loss on disposal", and
  "Write-off of inventories" should be entered as positive values unless the
  live template formula explicitly requires the opposite.
- Revenue goes to the Analysis sub-sheet breakdown (by type: goods, services, fees, etc.).
  The main sheet Revenue line may be a formula pulling from the sub-sheet.
- "Income" or "Income and Expenditure" in non-profit entities maps to Revenue/Expenses.
- Tax expense of zero should still be entered as 0 (not left blank) if disclosed.
- EPS (earnings per share) fields: only fill if the entity is a listed company with
  shares. CLG companies and entities without share capital skip EPS.
- For Function variant: "Administrative expenses" is a catch-all for operating expenses
  when the entity doesn't break them down by function.
- For Nature variant: "Other expenses" is the catch-all after employee benefits and
  depreciation are separated out.
