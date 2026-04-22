=== STATEMENT: SOCIE — SoRE (Statement of Retained Earnings, MPERS) ===

=== TEMPLATE STRUCTURE ===

Single sheet (`SoRE`) — a **simplified retained-earnings schedule** used by
MPERS filings that elect to present only the retained-earnings movement in
place of a full SOCIE matrix. The template is one tall column of values, not
a matrix; there are no per-component reserve columns.

**Column layout:**
- A: Label
- B: Current period value
- C: Prior period value
- (Group filings also fill D = Company CY, E = Company PY.)

**Row layout (single period stacked twice via the B/C columns):**
- Row 12: `Retained earnings at beginning of period`
- Row 13: `Impact of changes in accounting policies` (if any; otherwise blank)
- Row 14: `Retained earnings at beginning of period, restated` (FORMULA: row 12 + row 13)
- Row 16: `Profit (loss)` for the period
- Row 17: `*Total Profit (loss)` (FORMULA)
- Row 19: `Dividends paid` (negative — reduces retained earnings)
- Row 20: `*Total increase (decrease) in retained earnings` (FORMULA: row 17 + row 19)
- Row 21: `Retained earnings at end of period` (FORMULA: row 14 + row 20)

=== STRATEGY ===

1. Call `read_template()` to confirm the row numbers and labels above.
2. View the Statement of Retained Earnings page in the PDF.
3. Fill each data row using ROW + COL coordinates (B for CY, C for PY). Example:
   `{"sheet": "SoRE", "row": 12, "col": 2, "value": 4_200_000, "evidence": "..."}`
4. **Do NOT fill the `*Total…` formula rows** (17, 20, 21, 14) — they
   auto-calculate from the inputs you supply.
5. Call `fill_workbook()`, `verify_totals()` (status-only), then `save_result()`.

=== CRITICAL RULES ===

- **Dividends are NEGATIVE.** They reduce retained earnings. Enter as a
  negative number, not a positive one.
- **Closing retained earnings (row 21) must match SOFP "Retained earnings".**
  This is the SoRE cross-check — the one reconciliation that still runs after
  the SOCIE-consuming checks are gated out for SoRE filings.
- **Leave cells blank where there is no activity.** Do not enter zeros —
  especially on row 13 (accounting-policy impact) when no restatement applies.
- **No equity-component columns.** If the PDF shows a share-capital or
  reserves movement, that goes on a different statement (SOFP / SOCIE on
  standard MPERS) — not this sheet.
- **Two periods required:** fill both CY (col B) and PY (col C). Group
  filings additionally fill Company CY (col D) and Company PY (col E) from
  the standalone figures.
